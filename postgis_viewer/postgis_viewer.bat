@echo off
set OSGEO4W_ROOT=C:\OSGeo4W
PATH=%OSGEO4W_ROOT%\bin;%PATH%
for %%f in (%OSGEO4W_ROOT%\etc\ini\*.bat) do call %%f
set PYTHONPATH=C:\OSGeo4W\apps\qgis\python
set PATH=C:\OSGeo4W\apps\qgis\bin;%PATH%

start /B python "C:/Archivos de programa/PostgreSQL/8.4/bin\postgis_viewer\postgis_viewer.py" %*
