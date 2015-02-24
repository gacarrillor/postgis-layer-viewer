#!/usr/bin/python
# -*- coding: utf-8 -*-
"""
Basic PostGIS viewer based on QGIS libs. (v.1.6.1)
Usage: postgis_viewer.py <options>
    
Options:
    -h host
    -p port
    -U user
    -W password
    -d database
    -s schema
    -t table

Prerequisities:
    Qt, QGIS, libqt4-sql-psql

Using as PgAdmin plugin, copy 'postgis_viewer.py' file on PATH and put following 
to 'plugins.ini' (/usr/share/pgadmin3/plugins.ini on Debian):

    Title=View PostGIS layer
    Command=postgis_viewer.py -h $$HOSTNAME -p $$PORT -U $$USERNAME -W $$PASSWORD -d $$DATABASE -s $$SCHEMA -t $$OBJECTNAME
    Description=View PostGIS layer
    Platform=unix
    ServerType=postgresql
    Database=Yes
    SetPassword=Yes

Authors:
        Copyright (c) 2010 by Ivan Mincik, ivan.mincik@gista.sk
        Copyright (c) 2011-2015 Germán Carrillo, geotux_tuxman@linuxmail.org

License: GNU General Public License v2.0
"""

import os, sys, math, imp, fileinput, re
import getopt
import getpass, pickle # import stuff for ipc

try:
    from PyQt4.QtSql import QSqlDatabase, QSqlQuery
    from PyQt4.QtGui import ( QActionGroup, QAction, QMainWindow, QApplication, QMessageBox, 
        QStatusBar, QFrame, QLabel, QDockWidget, QTreeWidget, QTreeWidgetItem, 
        QPixmap, QIcon, QFont, QMenu, QColorDialog, QAbstractItemView, QTabWidget,
        QBitmap, QColor, QWidget )
    from PyQt4.QtCore import ( SIGNAL, Qt, QString, QSharedMemory, QIODevice, QPoint, 
        QObject, QSize )
    from PyQt4.QtNetwork import QLocalServer, QLocalSocket

    from qgis.core import ( QgsApplication, QgsDataSourceURI, QgsVectorLayer, 
        QgsRasterLayer, QgsMapLayerRegistry, QgsContrastEnhancement )
    from qgis.gui import QgsMapCanvas, QgsMapToolPan, QgsMapToolZoom, QgsMapCanvasLayer

except ImportError:
    print >> sys.stderr, 'E: Qt or QGIS not installed.'
    print >> sys.stderr, 'E: Exiting ...'
    sys.exit(1)

# Set the qgis_prefix and the imgs_dir according to the current os
qgis_prefix = ""
imgs_dir = ""
if os.name == "nt": # Windows
    qgis_prefix = "C:/OSGeo4W/apps/qgis/"
    imgs_dir = "postgis_viewer/imgs/"
else: # Linux
    qgis_prefix = "/usr"
    imgs_dir = "/usr/bin/postgis_viewer/imgs/"

class SingletonApp(QApplication):
    
    timeout = 1000
    
    def __init__(self, argv, application_id=None):
        QApplication.__init__(self, argv)
        
        self.socket_filename = unicode(os.path.expanduser("~/.ipc_%s" % self.generate_ipc_id()) )
        self.shared_mem = QSharedMemory()
        self.shared_mem.setKey(self.socket_filename)

        if self.shared_mem.attach():
            self.is_running = True
            return
        
        self.is_running = False
        if not self.shared_mem.create(1):
            print >>sys.stderr, "Unable to create single instance"
            return
        # start local server
        self.server = QLocalServer(self)
        # connect signal for incoming connections
        self.connect(self.server, SIGNAL("newConnection()"), self.receive_message)
        # if socket file exists, delete it
        if os.path.exists(self.socket_filename):
            os.remove(self.socket_filename)
        # listen
        self.server.listen(self.socket_filename)

    def __del__(self):
        self.shared_mem.detach()
        if not self.is_running:
            if os.path.exists(self.socket_filename):
                os.remove(self.socket_filename)

        
    def generate_ipc_id(self, channel=None):
        if channel is None:
            channel = os.path.basename(sys.argv[0])
        return "%s_%s" % (channel, getpass.getuser())

    def send_message(self, message):
        if not self.is_running:
            raise Exception("Client cannot connect to IPC server. Not running.")
        socket = QLocalSocket(self)
        socket.connectToServer(self.socket_filename, QIODevice.WriteOnly)
        if not socket.waitForConnected(self.timeout):
            raise Exception(str(socket.errorString()))
        socket.write(pickle.dumps(message))
        if not socket.waitForBytesWritten(self.timeout):
            raise Exception(str(socket.errorString()))
        socket.disconnectFromServer()
        
    def receive_message(self):
        socket = self.server.nextPendingConnection()
        if not socket.waitForReadyRead(self.timeout):
            print >>sys.stderr, socket.errorString()
            return
        byte_array = socket.readAll()
        self.handle_new_message(pickle.loads(str(byte_array)))

    def handle_new_message(self, message):
        self.emit( SIGNAL("loadPgLayer"), message )


class ViewerWnd( QMainWindow ):
    def __init__( self, app, dictOpts ):
        QMainWindow.__init__( self )
        self.setWindowTitle( "PostGIS Layer Viewer - v.1.6.1" )
        self.setTabPosition( Qt.BottomDockWidgetArea, QTabWidget.North )

        self.canvas = QgsMapCanvas()
        self.canvas.setCanvasColor( Qt.white )
        self.canvas.useImageToRender( True )
        self.canvas.enableAntiAliasing( True )
        self.setCentralWidget( self.canvas )

        actionZoomIn = QAction( QIcon( imgs_dir + "mActionZoomIn.png" ), QString( "Zoom in" ), self )
        actionZoomOut = QAction( QIcon( imgs_dir + "mActionZoomOut.png" ), QString( "Zoom out" ), self )
        actionPan = QAction( QIcon( imgs_dir + "mActionPan.png" ), QString( "Pan" ), self )
        actionZoomFullExtent = QAction( QIcon( imgs_dir + "mActionZoomFullExtent.png" ), QString( "Zoom full" ), self )

        actionZoomIn.setCheckable( True )
        actionZoomOut.setCheckable( True )
        actionPan.setCheckable( True )

        self.connect(actionZoomIn, SIGNAL( "triggered()" ), self.zoomIn )
        self.connect(actionZoomOut, SIGNAL( "triggered()" ), self.zoomOut )
        self.connect(actionPan, SIGNAL( "triggered()" ), self.pan )
        self.connect(actionZoomFullExtent, SIGNAL( "triggered()" ), self.zoomFullExtent )

        self.actionGroup = QActionGroup( self )
        self.actionGroup.addAction( actionPan )
        self.actionGroup.addAction( actionZoomIn )
        self.actionGroup.addAction( actionZoomOut )        

        # Create the toolbar
        self.toolbar = self.addToolBar( "Map tools" )
        self.toolbar.addAction( actionPan )
        self.toolbar.addAction( actionZoomIn )
        self.toolbar.addAction( actionZoomOut )
        self.toolbar.addAction( actionZoomFullExtent )

        # Create the map tools
        self.toolPan = QgsMapToolPan( self.canvas )
        self.toolPan.setAction( actionPan )
        self.toolZoomIn = QgsMapToolZoom( self.canvas, False ) # false = in
        self.toolZoomIn.setAction( actionZoomIn )
        self.toolZoomOut = QgsMapToolZoom( self.canvas, True ) # true = out
        self.toolZoomOut.setAction( actionZoomOut )
        
        # Create the statusbar
        self.statusbar = QStatusBar( self )
        self.statusbar.setObjectName( "statusbar" )
        self.setStatusBar( self.statusbar )

        self.lblXY = QLabel()
        self.lblXY.setFrameStyle( QFrame.Box )
        self.lblXY.setMinimumWidth( 170 )
        self.lblXY.setAlignment( Qt.AlignCenter )
        self.statusbar.setSizeGripEnabled( False )
        self.statusbar.addPermanentWidget( self.lblXY, 0 )

        self.lblScale = QLabel()
        self.lblScale.setFrameStyle( QFrame.StyledPanel )
        self.lblScale.setMinimumWidth( 140 )
        self.statusbar.addPermanentWidget( self.lblScale, 0 )

        self.createLegendWidget()   # Create the legend widget

        self.connect( app, SIGNAL( "loadPgLayer" ), self.loadLayer )
        self.connect( self.canvas, SIGNAL( "scaleChanged(double)" ),
            self.changeScale )
        self.connect( self.canvas, SIGNAL( "xyCoordinates(const QgsPoint&)" ),
            self.updateXY )

        self.pan() # Default

        self.plugins = Plugins( self, self.canvas, dictOpts['-h'], dictOpts['-p'], dictOpts['-d'], dictOpts['-U'], dictOpts['-W'] )

        self.createAboutWidget()
        self.layerSRID = '-1'
        self.loadLayer( dictOpts )
    
    def zoomIn( self ):
        self.canvas.setMapTool( self.toolZoomIn )

    def zoomOut( self ):
        self.canvas.setMapTool( self.toolZoomOut )

    def pan( self ):
        self.canvas.setMapTool( self.toolPan )

    def zoomFullExtent( self ):
        self.canvas.zoomToFullExtent()
    
    def about( self ):
        pass

    def createLegendWidget( self ):
        """ Create the map legend widget and associate it to the canvas """
        self.legend = Legend( self )
        self.legend.setCanvas( self.canvas )
        self.legend.setObjectName( "theMapLegend" )

        self.LegendDock = QDockWidget( "Layers", self )
        self.LegendDock.setObjectName( "legend" )
        self.LegendDock.setTitleBarWidget( QWidget() )
        self.LegendDock.setWidget( self.legend )
        self.LegendDock.setContentsMargins ( 0, 0, 0, 0 )
        self.addDockWidget( Qt.BottomDockWidgetArea, self.LegendDock )

    def createAboutWidget( self ):
        self.AboutDock = QDockWidget( "About", self )
        self.AboutDock.setObjectName( "about" )
        self.AboutDock.setTitleBarWidget( QWidget() )
        self.AboutDock.setContentsMargins( 0, 0, 0, 0 )
        self.tabifyDockWidget( self.LegendDock, self.AboutDock )
        self.LegendDock.raise_() # legendDock at the top

        from PyQt4.QtCore import QRect
        from PyQt4.QtGui import QSizePolicy, QGridLayout, QFont
        font = QFont()
        font.setFamily("Sans Serif")
        font.setPointSize(8.7)
        self.AboutWidget = QWidget()
        self.AboutWidget.setFont( font )
        self.AboutWidget.setObjectName("AboutWidget") 
        self.AboutDock.setWidget( self.AboutWidget )
        self.labelAbout = QLabel( self.AboutWidget )
        self.labelAbout.setAlignment(Qt.AlignCenter)
        self.labelAbout.setWordWrap(True)
        self.gridLayout = QGridLayout(self.AboutWidget)
        self.gridLayout.setContentsMargins(0, 0, 0, 0)
        self.gridLayout.setObjectName("gridLayout")
        self.gridLayout.addWidget(self.labelAbout, 0, 1, 1, 1)
        self.labelAbout.setTextInteractionFlags(Qt.LinksAccessibleByMouse|Qt.LinksAccessibleByKeyboard|Qt.TextSelectableByKeyboard|Qt.TextSelectableByMouse) 
        self.labelAbout.setOpenExternalLinks( True )
        self.labelAbout.setText("<html><head/><body><a href=\"http://geotux.tuxfamily.org/index.php/en/geo-blogs/item/293-consola-sql-para-plugin-pgadmin-postgis-viewer\">PostGIS Layer Viewer</a> v.1.6.1 (2015.02.24)<br \><br \>" \
            "Copyright (c) 2010 Ivan Mincik,<br \>ivan.mincik@gista.sk<br \>" \
            u"Copyright (c) 2011-2015 Germán Carrillo,<br \>geotux_tuxman@linuxmail.org<br \><br \>" \
            "<i>Licensed under the terms of GNU GPL v.2.0</i><br \><br \>" \
            "Based on PyQGIS. Plugin Fast SQL Layer by Pablo T. Carreira.</body></html>" )

    def loadLayer( self, dictOpts ):
        print 'I: Loading the layer...'
        self.layerSRID = dictOpts[ 'srid' ] # To access the SRID when querying layer properties

        if not self.isActiveWindow():
            self.activateWindow()            
            self.raise_() 

        if dictOpts['type'] == 'vector':
            # QGIS connection
            uri = QgsDataSourceURI()
            uri.setConnection( dictOpts['-h'], dictOpts['-p'], dictOpts['-d'], 
                dictOpts['-U'], dictOpts['-W'] )
            uri.setDataSource( dictOpts['-s'], dictOpts['-t'], dictOpts['-g'] )
            layer = QgsVectorLayer( uri.uri(), dictOpts['-s'] + '.' + dictOpts['-t'],
                "postgres" )        
        elif dictOpts['type'] == 'raster':          
            connString = "PG: dbname=%s host=%s user=%s password=%s port=%s mode=2 " \
                "schema=%s column=%s table=%s" % ( dictOpts['-d'], dictOpts['-h'], 
                dictOpts['-U'], dictOpts['-W'], dictOpts['-p'], dictOpts['-s'], 
                dictOpts['col'], dictOpts['-t'] )
            layer = QgsRasterLayer( connString, dictOpts['-s'] + '.' + dictOpts['-t'] )
            
            if layer.isValid():
                layer.setContrastEnhancement( QgsContrastEnhancement.StretchToMinimumMaximum )

        self.addLayer( layer, self.layerSRID )

    def addLayer( self, layer, srid='-1' ):
        if layer.isValid():
            # Only in case that srid != -1, read the layer SRS properties, otherwise don't since it will return 4326
            if srid != '-1': 
               self.layerSRID = layer.crs().description() + ' (' + str( layer.crs().postgisSrid() ) + ')'
            else:
               self.layerSRID = 'Unknown SRS (-1)'

            if self.canvas.layerCount() == 0:
                self.canvas.setExtent( layer.extent() )

                if srid != '-1':
                    print 'I: Map SRS (EPSG): %s' % self.layerSRID                    
                    self.canvas.setMapUnits( layer.crs().mapUnits() )
                else:
                    print 'I: Unknown Reference System'
                    self.canvas.setMapUnits( 0 ) # 0: QGis.Meters

            return QgsMapLayerRegistry.instance().addMapLayer( layer )
        return False

    def activeLayer( self ):
        """ Returns the active layer in the layer list widget """
        return self.legend.activeLayer()

    def getLayerProperties( self, l ):
        """ Create a layer-properties string (l:layer)"""
        print 'I: Generating layer properties...'
        if l.type() == 0: # Vector
            wkbType = ["WKBUnknown","WKBPoint","WKBLineString","WKBPolygon",
                       "WKBMultiPoint","WKBMultiLineString","WKBMultiPolygon",
                       "WKBNoGeometry","WKBPoint25D","WKBLineString25D","WKBPolygon25D",
                       "WKBMultiPoint25D","WKBMultiLineString25D","WKBMultiPolygon25D"]
            properties = "Source: %s\n" \
                         "Geometry type: %s\n" \
                         "Number of features: %s\n" \
                         "Number of fields: %s\n" \
                         "SRS (EPSG): %s\n" \
                         "Extent: %s " \
                          % ( l.source(), wkbType[l.wkbType()], l.featureCount(), 
                              l.dataProvider().fields().count(), self.layerSRID, 
                              l.extent().toString() )
        elif l.type() == 1: # Raster
            rType = [ "GrayOrUndefined (single band)", "Palette (single band)", "Multiband", "ColorLayer" ]
            properties = "Source: %s\n" \
                         "Raster type: %s\n" \
                         "Width-Height (pixels): %sx%s\n" \
                         "Bands: %s\n" \
                         "SRS (EPSG): %s\n" \
                         "Extent: %s" \
                         % ( l.source(), rType[l.rasterType()], l.width(), l.height(),
                             l.bandCount(), self.layerSRID, l.extent().toString() )

        self.layerSRID = '-1' # Initialize the srid 
        return properties

    def changeScale( self, scale ):
        self.lblScale.setText( "Scale 1:" + formatNumber( scale ) )

    def updateXY( self, p ):
        if self.canvas.mapUnits() == 2: # Degrees
            self.lblXY.setText( formatToDegrees( p.x() ) + " | " \
                + formatToDegrees( p.y() ) )
        else: # Unidad lineal
            self.lblXY.setText( formatNumber( p.x() ) + " | " \
                + formatNumber( p.y() ) + "" )


# Class to expose qgis objects and functionalities to plugins
class QgisInterface( QObject ):
    """ Class to expose qgis objects and functionalities to plugins """
    def __init__( self, myApp, canvas ):
        QObject.__init__( self )
        self.myApp = myApp
        self.canvas = canvas
        self.toolBarPlugins = None
        
    def addVectorLayer( self, vectorLayerPath, baseName, providerKey, srid=-1):
        layer = QgsVectorLayer( vectorLayerPath, baseName, providerKey ) 
        return self.myApp.addLayer( layer, srid )         

    def zoomFull( self ):
        """ Zoom to the map full extent """
        self.canvas.zoomToFullExtent()

    def zoomToPrevious( self ):
        """ Zoom to previous view extent """
        self.canvas.zoomToPreviousExtent()

    def zoomToNext( self ):
        """ Zoom to next view extent """
        self.canvas.zoomToNextExtent()

    def activeLayer( self ):
        """ Get pointer to the active layer (layer selected in the legend) """
        if self.myApp.activeLayer():
            return self.myApp.activeLayer().layer()
        return None

    def addToolBarIcon( self, qAction ):
        """ Add an icon to the plugins toolbar """
        if not self.toolBarPlugins:
            self.toolBarPlugins = self.addToolBar( "Plugins" )
        self.toolBarPlugins.addAction( qAction )

    def removeToolBarIcon( self, qAction ):
        """ Remove an action (icon) from the plugin toolbar """
        if not self.toolBarPlugins:
            self.toolBarPlugins = self.addToolBar( "Plugins" )
        self.toolBarPlugins.removeAction( qAction )

    def addToolBar( self, name ):
        """ Add toolbar with specified name """
        toolBar = self.myApp.addToolBar( name )
        toolBar.setObjectName( name )
        return toolBar

    def mapCanvas( self ):
        """ Return a pointer to the map canvas """
        return self.canvas

    def mainWindow( self ):
        """ Return a pointer to the main window (instance of QgisApp in case of QGIS) """
        return self.myApp

    def addDockWidget( self, area, dockwidget ):
        """ Add a dock widget to the main window """
        dockwidget.setTitleBarWidget( QWidget() )
        self.myApp.tabifyDockWidget( self.myApp.LegendDock, dockwidget )
        self.myApp.LegendDock.raise_() # legendDock at the top

# Class to manage plugins (Read and load the existing plugins)
class Plugins():
    """ Class to manage plugins (Read and load existing plugins) """
    def __init__( self, myApp, canvas, host, port, dbname, user, passwd ):
        self.qgisInterface = QgisInterface( myApp, canvas )
        self.myApp = myApp
        self.plugins = []
        self.pluginsDirName = 'plugins'
        regExpString = '^def +classFactory\(.*iface.*(\):)$' # To find the classFactory line

        """ Validate that it is a plugins' folder and load them into the application """
        dir_plugins = os.path.join( os.path.dirname(__file__), self.pluginsDirName )

        if os.path.exists( dir_plugins ):
            for root, dirs, files in os.walk( dir_plugins ):
                bPlugIn = False

                if not dir_plugins == root: 
                    if '__init__.py' in files: 
                        for line in fileinput.input( os.path.join( root, '__init__.py' ) ):
                            linea = line.strip()
                            if re.match( regExpString, linea ):
                                bPlugIn = True
                                break
                        fileinput.close()

                        if bPlugIn:
                            plugin_name = os.path.basename( root )
                            f, filename, description = imp.find_module( plugin_name, [ dir_plugins ] )

                            try: 
                                package = imp.load_module( plugin_name, f, filename, description )
                                self.plugins.append( package.classFactory( self.qgisInterface, host, port, dbname, user, passwd ) )
                            except Exception, e:
                                print 'E: Plugin ' + plugin_name + ' could not be loaded. ERROR!:',e
                            else:
                                self.plugins[ -1 ].initGui()
                                print 'I: Plugin ' + plugin_name + ' successfully loaded!'
        else:
            print "Plugins folder not found."

# A couple of classes for the layer list widget and the layer properties
class LegendItem( QTreeWidgetItem ):
    """ Provide a widget to show and manage the properties of one single layer """
    def __init__( self, parent, canvasLayer ):
        QTreeWidgetItem.__init__( self )
        self.legend = parent
        self.canvasLayer = canvasLayer
        self.canvasLayer.layer().setLayerName( self.legend.normalizeLayerName( unicode( self.canvasLayer.layer().name() ) ) )
        self.setText( 0, self.canvasLayer.layer().name() )
        self.isVect = ( self.canvasLayer.layer().type() == 0 ) # 0: Vector, 1: Raster
        self.layerId = self.canvasLayer.layer().id()

        if self.isVect:
            geom = self.canvasLayer.layer().dataProvider().geometryType()

        self.setCheckState( 0, Qt.Checked )

        if self.isVect:
            pm = QPixmap( 20, 20 )
            icon = QIcon()
            if geom == 1 or geom == 4 or geom == 8 or geom == 11: # Point
                icon.addPixmap( QPixmap( imgs_dir + "mIconPointLayer.png" ), QIcon.Normal, QIcon.On)
            elif geom == 2 or geom == 5 or geom == 9 or geom == 12: # Polyline
                icon.addPixmap( QPixmap( imgs_dir + "mIconLineLayer.png"), QIcon.Normal, QIcon.On)
            elif geom == 3 or geom == 6 or geom == 10 or geom == 13: # Polygon
                icon.addPixmap( QPixmap( imgs_dir + "mIconPolygonLayer.png"), QIcon.Normal, QIcon.On)
            else: # Not a valid WKT Geometry
                geom = self.canvasLayer.layer().geometryType() # QGis Geometry
                if geom == 0: # Point
                    icon.addPixmap( QPixmap( imgs_dir + "mIconPointLayer.png" ), QIcon.Normal, QIcon.On)
                elif geom == 1: # Line
                    icon.addPixmap( QPixmap( imgs_dir + "mIconLineLayer.png"), QIcon.Normal, QIcon.On)
                elif geom == 2: # Polygon
                    icon.addPixmap( QPixmap( imgs_dir + "mIconPolygonLayer.png"), QIcon.Normal, QIcon.On)
                else:
                    raise RuntimeError, 'Unknown geometry: ' + str( geom )

        else:
            qimg = self.canvasLayer.layer().previewAsImage( QSize(20,20) )
            icon = QIcon( QBitmap().fromImage( qimg ) )

        self.setIcon( 0, icon )

        #self.setToolTip( 0, self.canvasLayer.layer().publicSource() )
        layerFont = QFont()
        layerFont.setBold( True )
        self.setFont( 0, layerFont )

        # Display layer properties
        self.properties = self.legend.pyQGisApp.getLayerProperties( self.canvasLayer.layer() )
        self.child = QTreeWidgetItem( self )
        self.child.setFlags( Qt.NoItemFlags ) # Avoid the item to be selected
        self.displayLayerProperties()
        
    def displayLayerProperties( self ):
        """ It is required to build the QLabel widget every time it is set """        
        propertiesFont = QFont()
        propertiesFont.setItalic( True )
        propertiesFont.setPointSize( 8 )
        propertiesFont.setStyleStrategy( QFont.PreferAntialias )

        label = QLabel( self.properties )
        label.setTextInteractionFlags( Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard )
        label.setFont( propertiesFont )
        self.legend.setItemWidget( self.child, 0, label )
        
    def nextSibling( self ):
        """ Return the next layer item """
        return self.legend.nextSibling( self )

    def storeAppearanceSettings( self ):
        """ Store the appearance of the layer item """
        self.__itemIsExpanded = self.isExpanded()

    def restoreAppearanceSettings( self ):
        """ Restore the appearance of the layer item """
        self.setExpanded( self.__itemIsExpanded )
        self.displayLayerProperties() # Generate the QLabel widget again
        

class Legend( QTreeWidget ):
    """
      Provide a widget that manages map layers and their properties as tree items
    """
    def __init__( self, parent ):
        QTreeWidget.__init__( self, parent )

        self.pyQGisApp = parent
        self.canvas = None
        self.layers = self.getLayerSet()

        self.bMousePressedFlag = False
        self.itemBeingMoved = None

        # QTreeWidget properties
        self.setSortingEnabled( False )
        self.setDragEnabled( False )
        self.setAutoScroll( False )
        #self.setVerticalScrollMode( QAbstractItemView.ScrollPerPixel )
        self.setHeaderHidden( True )
        self.setRootIsDecorated( True )
        self.setContextMenuPolicy( Qt.CustomContextMenu )

        self.connect( self, SIGNAL( "customContextMenuRequested(QPoint)" ),
            self.showMenu )
        self.connect( QgsMapLayerRegistry.instance(), SIGNAL("layerWasAdded(QgsMapLayer *)"),
            self.addLayerToLegend )
        self.connect( QgsMapLayerRegistry.instance(), SIGNAL( "removedAll()" ),
            self.removeAll )
        self.connect( self, SIGNAL( "itemChanged(QTreeWidgetItem *,int)" ),
            self.updateLayerStatus )
        self.connect( self, SIGNAL( "currentItemChanged(QTreeWidgetItem *, QTreeWidgetItem *)" ),
            self.currentItemChanged )
            
    def setCanvas( self, canvas ):
        """ Set the base canvas """
        self.canvas = canvas

    def showMenu( self, pos ):
        """ Show a context menu for the active layer in the legend """
        item = self.itemAt( pos )
        if item:
            if self.isLegendLayer( item ):
                self.setCurrentItem( item )
                self.menu = self.getMenu( item.isVect, item.canvasLayer )
                self.menu.popup( QPoint( self.mapToGlobal( pos ).x() + 5, self.mapToGlobal( pos ).y() ) )

    def getMenu( self, isVect, canvasLayer ):
        """ Create a context menu for a layer """
        menu = QMenu()
        menu.addAction( QIcon( imgs_dir + "mActionZoomToLayer.png" ), "&Zoom to layer extent", self.zoomToLayer )
        menu.addSeparator()
        if isVect :
            menu.addAction( QIcon( imgs_dir + "symbology.png" ), "&Symbology...", self.layerSymbology )
        menu.addSeparator()
        menu.addAction( QIcon( imgs_dir + "collapse.png" ), "&Collapse all", self.collapseAll )
        menu.addAction( QIcon( imgs_dir + "expand.png" ), "&Expand all", self.expandAll )
        menu.addSeparator()
        menu.addAction( QIcon( imgs_dir + "removeLayer.png" ), "&Remove layer", self.removeCurrentLayer )
        return menu

    def mousePressEvent(self, event):
        """ Mouse press event to manage the layers drag """
        if ( event.button() == Qt.LeftButton ):
            self.lastPressPos = event.pos()
            self.bMousePressedFlag = True
        QTreeWidget.mousePressEvent( self, event )

    def mouseMoveEvent(self, event):
        """ Mouse move event to manage the layers drag """
        if ( self.bMousePressedFlag ):
            # Set the flag back such that the else if(itemBeingMoved)
            # code part is passed during the next mouse moves
            self.bMousePressedFlag = False

            # Remember the item that has been pressed
            item = self.itemAt( self.lastPressPos )
            if ( item ):
                if ( self.isLegendLayer( item ) ):
                    self.itemBeingMoved = item
                    self.storeInitialPosition() # Store the initial layers order
                    self.setCursor( Qt.SizeVerCursor )
                else:
                    self.setCursor( Qt.ForbiddenCursor )
        elif ( self.itemBeingMoved ):
            p = QPoint( event.pos() )
            self.lastPressPos = p

            # Change the cursor
            item = self.itemAt( p )
            origin = self.itemBeingMoved
            dest = item

            if not item:
                self.setCursor( Qt.ForbiddenCursor )

            if ( item and ( item != self.itemBeingMoved ) ):
                if ( self.yCoordAboveCenter( dest, event.y() ) ): # Above center of the item
                    if self.isLegendLayer( dest ): # The item is a layer
                        if ( origin.nextSibling() != dest ):                            
                            self.moveItem( dest, origin )
                        self.setCurrentItem( origin )
                        self.setCursor( Qt.SizeVerCursor )
                    else:
                        self.setCursor( Qt.ForbiddenCursor )
                else: # Below center of the item
                    if self.isLegendLayer( dest ): # The item is a layer
                        if ( self.itemBeingMoved != dest.nextSibling() ):
                            self.moveItem( origin, dest )
                        self.setCurrentItem( origin )
                        self.setCursor( Qt.SizeVerCursor )
                    else:
                        self.setCursor( Qt.ForbiddenCursor )

    def mouseReleaseEvent( self, event ):
        """ Mouse release event to manage the layers drag """
        QTreeWidget.mouseReleaseEvent( self, event )
        self.setCursor( Qt.ArrowCursor )
        self.bMousePressedFlag = False

        if ( not self.itemBeingMoved ):
            #print "*** Legend drag: No itemBeingMoved ***"
            return

        dest = self.itemAt( event.pos() )
        origin = self.itemBeingMoved
        if ( ( not dest ) or ( not origin ) ): # Release out of the legend
            self.checkLayerOrderUpdate()
            return

        self.checkLayerOrderUpdate()
        self.itemBeingMoved = None

    def addLayerToLegend( self, canvasLayer ):
        """ Slot. Create and add a legend item based on a layer """
        legendLayer = LegendItem( self, QgsMapCanvasLayer( canvasLayer ) )
        self.addLayer( legendLayer )

    def addLayer( self, legendLayer ):
        """ Add a legend item to the legend widget """
        self.insertTopLevelItem ( 0, legendLayer )
        self.expandItem( legendLayer )
        self.setCurrentItem( legendLayer )
        self.updateLayerSet()

    def updateLayerStatus( self, item ):
        """ Update the layer status """
        if ( item ):
            if self.isLegendLayer( item ): # Is the item a layer item?
                for i in self.layers:
                    if i.layer().id() == item.layerId:
                        if item.checkState( 0 ) == Qt.Unchecked:
                            i.setVisible( False )
                        else:
                            i.setVisible( True )
                        self.canvas.setLayerSet( self.layers )
                        return

    def currentItemChanged( self, newItem, oldItem ):
        """ Slot. Capture a new currentItem and emit a SIGNAL to inform the new type 
            It could be used to activate/deactivate GUI buttons according the layer type
        """
        layerType = None

        if self.currentItem():
            if self.isLegendLayer( newItem ):
                layerType = newItem.canvasLayer.layer().type()
                self.canvas.setCurrentLayer( newItem.canvasLayer.layer() )
            else:
                layerType = newItem.parent().canvasLayer.layer().type()
                self.canvas.setCurrentLayer( newItem.parent().canvasLayer.layer() )

        self.emit( SIGNAL( "activeLayerChanged" ), layerType )

    def zoomToLayer( self ):
        """ Slot. Manage the zoomToLayer action in the context Menu """
        self.zoomToLegendLayer( self.currentItem() )

    def removeCurrentLayer( self ):
        """ Slot. Manage the removeCurrentLayer action in the context Menu """
        QgsMapLayerRegistry.instance().removeMapLayer( self.currentItem().canvasLayer.layer().id() )
        self.removeLegendLayer( self.currentItem() )
        self.updateLayerSet()

    def layerSymbology( self ):
        """ Change the features color of a vector layer """
        legendLayer = self.currentItem()
        
        if legendLayer.isVect == True:
            geom = legendLayer.canvasLayer.layer().geometryType() # QGis Geometry
            for i in self.layers:
                if i.layer().id() == legendLayer.layerId:
                    color = QColorDialog.getColor( i.layer().rendererV2().symbols()[ 0 ].color(), self.pyQGisApp )
                    break
            if color.isValid():
                pm = QPixmap()
                iconChild = QIcon()
                legendLayer.canvasLayer.layer().rendererV2().symbols()[ 0 ].setColor( color )                                       
                self.canvas.refresh()

    def zoomToLegendLayer( self, legendLayer ):
        """ Zoom the map to a layer extent """
        for i in self.layers:
            if i.layer().id() == legendLayer.layerId:
                extent = i.layer().extent()
                extent.scale( 1.05 )
                self.canvas.setExtent( extent )
                self.canvas.refresh()
                break

    def removeLegendLayer( self, legendLayer ):
        """ Remove a layer item in the legend """
        if self.topLevelItemCount() == 1:
            self.clear()
        else: # Manage the currentLayer before the remove
            indice = self.indexOfTopLevelItem( legendLayer )
            if indice == 0:
                newCurrentItem = self.topLevelItem( indice + 1 )
            else:
                newCurrentItem = self.topLevelItem( indice - 1 )

            self.setCurrentItem( newCurrentItem )
            self.takeTopLevelItem( self.indexOfTopLevelItem( legendLayer ) )

    def removeAll( self ):
        """ Remove all legend items """
        self.clear()
        self.updateLayerSet()

    def updateLayerSet( self ):
        """ Update the LayerSet and set it to canvas """
        self.layers = self.getLayerSet()
        self.canvas.setLayerSet( self.layers )

    def getLayerSet( self ):
        """ Get the LayerSet by reading the layer items in the legend """
        layers = []
        for i in range( self.topLevelItemCount() ):
            layers.append( self.topLevelItem( i ).canvasLayer )
        return layers

    def activeLayer( self ):
        """ Return the selected layer """
        if self.currentItem():
            if self.isLegendLayer( self.currentItem() ):
                return self.currentItem().canvasLayer
            else:
                return self.currentItem().parent().canvasLayer
        else:
            return None

    def collapseAll( self ):
        """ Collapse all layer items in the legend """
        for i in range( self.topLevelItemCount() ):
            item = self.topLevelItem( i )
            self.collapseItem( item )

    def expandAll( self ):
        """ Expand all layer items in the legend """
        for i in range( self.topLevelItemCount() ):
            item = self.topLevelItem( i )
            self.expandItem( item )

    def isLegendLayer( self, item ):
        """ Check if a given item is a layer item """
        return not item.parent()

    def storeInitialPosition( self ):
        """ Store the layers order """
        self.__beforeDragStateLayers = self.getLayerIDs()

    def getLayerIDs( self ):
        """ Return a list with the layers ids """
        layers = []
        for i in range( self.topLevelItemCount() ):
            item = self.topLevelItem( i )
            layers.append( item.layerId )
        return layers

    def nextSibling( self, item ):
        """ Return the next layer item based on a given item """
        for i in range( self.topLevelItemCount() ):
            if item.layerId == self.topLevelItem( i ).layerId:
                break
        if i < self.topLevelItemCount():                                            
            return self.topLevelItem( i + 1 )
        else:
            return None

    def moveItem( self, itemToMove, afterItem ):
        """ Move the itemToMove after the afterItem in the legend """
        itemToMove.storeAppearanceSettings() # Store settings in the moved item
        self.takeTopLevelItem( self.indexOfTopLevelItem( itemToMove ) )
        self.insertTopLevelItem( self.indexOfTopLevelItem( afterItem ) + 1, itemToMove )
        itemToMove.restoreAppearanceSettings() # Apply the settings again
        self.updatePropertiesWidget() # Regenerate all the QLabel widgets for displaying purposes

    def updatePropertiesWidget(self):
        """ Weird function to create QLabel widgets for refreshing the properties 
            It is required to avoid a disgusting overlap in QLabel widgets
        """
        for i in range( self.topLevelItemCount() ):
            item = self.topLevelItem( i )
            item.displayLayerProperties()
            
    def checkLayerOrderUpdate( self ):
        """
            Check if the initial layers order is equal to the final one.
            This is used to refresh the legend in the release event.
        """
        self.__afterDragStateLayers = self.getLayerIDs()
        if self.__afterDragStateLayers != self.__beforeDragStateLayers:
            self.updateLayerSet()
            
    def yCoordAboveCenter( self, legendItem, ycoord ):
        """
            Return a bool to know if the ycoord is in the above center of the legendItem

            legendItem: The base item to get the above center and the below center
            ycoord: The coordinate of the comparison
        """
        rect = self.visualItemRect( legendItem )
        height = rect.height()
        top = rect.top()
        mid = top + ( height / 2 )
        if ( ycoord > mid ): # Bottom, remember the y-coordinate increases downwards
            return False
        else: # Top
            return True

    def normalizeLayerName( self, name ):
        """ Create an alias to put in the legend and avoid to repeat names """
        # Remove the extension
        if len( name ) > 4:
            if name[ -4 ] == '.':
                name = name[ :-4 ]
        return self.createUniqueName( name )

    def createUniqueName( self, name ):
        """ Avoid to repeat layers names """
        import re
        name_validation = re.compile( "\s\(\d+\)$", re.UNICODE ) # Strings like " (1)"

        bRepetida = True
        i = 1
        while bRepetida:
            bRepetida = False

            # If necessary add a sufix like " (1)" to avoid to repeat names in the legend
            for j in range( self.topLevelItemCount() ):
                item = self.topLevelItem( j )
                if item.text( 0 ) == name:
                    bRepetida = True
                    if name_validation.search( name ): # The name already has numeration
                        name = name[ :-4 ]  + ' (' + str( i ) + ')'
                    else: # Add numeration because the name doesn't have it
                        name = name + ' (' + str( i ) + ')'
                    i += 1
        return name


# Some helpful functions
def formatNumber( number, precision=0, group_sep='.', decimal_sep=',' ):
    """
        number: Number to be formatted 
        precision: Number of decimals
        group_sep: Miles separator
        decimal_sep: Decimal separator
    """
    number = ( '%.*f' % ( max( 0, precision ), number ) ).split( '.' )
    integer_part = number[ 0 ]

    if integer_part[ 0 ] == '-':
        sign = integer_part[ 0 ]
        integer_part = integer_part[ 1: ]
    else:
        sign = ''

    if len( number ) == 2:
        decimal_part = decimal_sep + number[ 1 ]
    else:
        decimal_part = ''

    integer_part = list( integer_part )
    c = len( integer_part )

    while c > 3:
        c -= 3
        integer_part.insert( c, group_sep )

    return sign + ''.join( integer_part ) + decimal_part

def formatToDegrees( number ):
    """ Returns the degrees-minutes-seconds form of number """
    sign = ''
    if number < 0:
        number = math.fabs( number )
        sign = '-'

    deg = math.floor( number )
    minu = math.floor( ( number - deg ) * 60 )
    sec = ( ( ( number - deg ) * 60 ) - minu ) * 60 

    return sign + "%.0f"%deg + u'° ' + "%.0f"%minu + "' " \
        + "%.2f"%sec + "\""

def show_error(title, text):
    QMessageBox.critical(None, title, text,
    QMessageBox.Ok | QMessageBox.Default,
    QMessageBox.NoButton)
    print >> sys.stderr, 'E: Error. Exiting ...'
    print __doc__
    sys.exit(1)


def main( argv ):
    print 'I: Starting viewer ...'    
    app = SingletonApp( argv )

    dictOpts = { '-h':'', '-p':'5432', '-U':'', '-W':'', '-d':'', '-s':'public', 
                  '-t':'', '-g':'', 'type':'unknown', 'srid':'', 'col':'' }

    opts, args = getopt.getopt( sys.argv[1:], 'h:p:U:W:d:s:t:g:', [] )
    dictOpts.update( opts )
    
    if dictOpts['-t'] == '':
        print >> sys.stderr, 'E: Table name is required'
        print __doc__
        sys.exit( 1 )

    d = QSqlDatabase.addDatabase( "QPSQL", "PgSQLDb" )
    d.setHostName( dictOpts['-h'] )
    d.setPort( int( dictOpts['-p'] ) )
    d.setDatabaseName( dictOpts['-d'] )
    d.setUserName( dictOpts['-U'] )
    d.setPassword( dictOpts['-W'] )

    if d.open():
        print 'I: Database connection was succesfull'
        
        query = QSqlQuery( d )
        query.exec_( "SELECT Count(srid) FROM raster_columns WHERE r_table_schema = '%s' AND r_table_name = '%s'" % ( dictOpts['-s'], dictOpts['-t'] ) )
        
        if query.next() and query.value( 0 ).toBool(): # Raster layer (WKTRaster)!            
            query.exec_( "SELECT srid, r_raster_column FROM raster_columns \
                          WHERE r_table_schema = '%s' AND \
                          r_table_name = '%s' " % ( dictOpts['-s'], dictOpts['-t'] ) )
            if query.next():
                dictOpts[ 'srid' ] = str( query.value( 0 ).toString() )
                dictOpts[ 'col' ] = str( query.value( 1 ).toString() )

            dictOpts['type'] = 'raster'
            print 'I: Raster layer detected'
            
        else: # Vector layer?            
            query.exec_( "SELECT column_name FROM information_schema.columns \
                    WHERE table_schema = '%s' AND \
                    table_name = '%s' AND \
                    udt_name = 'geometry' LIMIT 1" % ( dictOpts['-s'], dictOpts['-t'] ) )          

            if not query.next(): # Geography layer?        
                query.exec_( "SELECT column_name FROM information_schema.columns \
                        WHERE table_schema = '%s' AND \
                        table_name = '%s' AND \
                        udt_name = 'geography' LIMIT 1" % ( dictOpts['-s'], dictOpts['-t'] ) )

            if query.first(): # Vector layer!        
                dictOpts[ '-g' ] = str( query.value( 0 ).toString() )

                query.exec_( "SELECT srid FROM geometry_columns \
                              WHERE f_table_schema = '%s' AND \
                              f_table_name = '%s' " % ( dictOpts['-s'], dictOpts['-t'] ) )
                if query.next():
                    dictOpts[ 'srid' ] = str( query.value( 0 ).toString() )

                dictOpts['type'] = 'vector'
                print 'I: Vector layer detected'

        if not dictOpts[ 'type' ] == 'unknown': # The object is a layer
            if app.is_running:
                # Application already running, send message to load data
                app.send_message( dictOpts )
            else:
                # Start the Viewer

                # QGIS libs init
                QgsApplication.setPrefixPath(qgis_prefix, True)
                QgsApplication.initQgis()

                # Open viewer
                wnd = ViewerWnd( app, dictOpts )
                wnd.move(100,100)
                wnd.resize(400, 500)
                wnd.show()

                retval = app.exec_()

                # Exit
                QgsApplication.exitQgis()
                print 'I: Exiting ...'
                sys.exit(retval)      
        else:
            show_error("Error when opening layer", 
                "Layer '%s.%s' doesn't exist. Be sure the selected object is either raster or vector layer." % (dictOpts['-s'], dictOpts['-t']))
    else:
        show_error("Connection error", "Error when connecting to database.")

if __name__ == "__main__":
    main( sys.argv )
