"""
/***************************************************************************
 Fast SQL Layer
                                 A QGIS plugin
 Just type the query to add the layer
                              -------------------
        begin                : 2011-05-12
        copyright            : (C) 2011 by Pablo Torres Carreira
        email                : pablotcarreira@hotmail.com
 ***************************************************************************/

/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
"""
# Import the PyQt and QGIS libraries
from PyQt4 import uic
from PyQt4.QtCore import *
from PyQt4.QtGui import *
from qgis.core import *

import highlighter as hl
import os, re
import resources

import postgis_utils 
# Initialize Qt resources from file resources.py


class PostgisLayer:
    def __init__(self, iface, host, port, dbname, user, passwd):
        # Save reference to the QGIS interface
        self.iface = iface
        self.host = host 
        self.port = port
        self.dbname = dbname
        self.user = user
        self.passwd = passwd

    def initGui(self):
        # Create action that will start plugin configuration
        self.action = QAction(QIcon(":/plugins/postgislayer/icon.png"), "Fast SQL Layer", self.iface.mainWindow())
        #Add toolbar button and menu item
        self.iface.addToolBarIcon(self.action)
        
        
        #load the form  
        path = os.path.dirname(os.path.abspath(__file__))
        self.dock = uic.loadUi(os.path.join(path, "ui_postgislayer.ui"))
        self.iface.addDockWidget(Qt.BottomDockWidgetArea, self.dock)
        
        
        #connect the action to the run method
        QObject.connect(self.action, SIGNAL("triggered()"), self.show)
        QObject.connect(self.dock.buttonRun, SIGNAL('clicked()'), self.run)        
        
        #populate the id and the_geom combos
        self.dock.uniqueCombo.addItem('id')
        self.dock.geomCombo.addItem('the_geom')
                
        #start the highlight engine
        self.higlight_text = hl.Highlighter(self.dock.textQuery.document(), "sql")
        
    def show(self):
        self.iface.addDockWidget(Qt.BottomDockWidgetArea, self.dock)
    
    def unload(self):
        # Remove the plugin menu item and icon
        self.iface.removeToolBarIcon(self.action)

    
    def run(self):
		try:
			import psycopg2
		except ImportError, e:
			QMessageBox.information(self.iface.mainWindow(), "Warning", "Couldn't import Python module 'psycopg2' for communication with PostgreSQL database. Without it you won't be able to run this tool. Please install it.")
			return

		uniqueFieldName = self.dock.uniqueCombo.currentText()
		geomFieldName = self.dock.geomCombo.currentText()

		try:
			db = postgis_utils.GeoDB( self.host, int(self.port), self.dbname, self.user, self.passwd )
		except postgis_utils.DbError, e:
			QMessageBox.critical(self.iface.mainWindow(), "error", "Couldn't connect to database:\n"+e.msg)
			return

		uri = QgsDataSourceURI()
		uri.setConnection(self.host, self.port, self.dbname, self.user, self.passwd)

		query = str(self.dock.textQuery.toPlainText()).lstrip().replace(";","")

		# Validate query
		if not re.match("^SELECT", query.upper() ):
			QMessageBox.critical(self.iface.mainWindow(), "error", "The query has to be a SELECT clause.")
			return 
		try:
			db._exec_sql( db.con.cursor(), query )
		except postgis_utils.DbError, e:
			QMessageBox.critical(self.iface.mainWindow(), "error", str(e))
			return 

		QApplication.setOverrideCursor(QCursor(Qt.WaitCursor))

		# Get srid 
		try:
			srid = db.get_srid_from_geom( geomFieldName, query )
		except postgis_utils.DbError, e:
			QMessageBox.critical(self.iface.mainWindow(), "error", e.msg)
			return 

		#lstrip() is needed to remove spaces in the first line.
		uri.setDataSource("", "(" + query + ")", geomFieldName, "", uniqueFieldName)
		vl = self.iface.addVectorLayer(uri.uri(), "QueryLayer", "postgres", srid)
		if not vl:
			QMessageBox.information(self.iface.mainWindow(), "Warning", "Couldn't load" + \
			  "the layer. It doesn't seem to be a valid layer.")
		QApplication.restoreOverrideCursor()
 
