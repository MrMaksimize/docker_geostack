"""Geospatial Utilities."""
import os
import logging
from poseidon.util import general
import requests
import csv
import json
import pandas as pd
import numpy as np
import geopandas as gpd
from geopandas.tools import sjoin
from shapely.geometry import Point
from shapely.wkt import loads
from shapely.geometry import mapping
import fiona
from fiona import crs
import pymssql
import zipfile
from osgeo import ogr
from osgeo import osr
import geojson
import geobuf
import gzip
import shutil

conf = general.config


def geocode_address_google(address_line='',
                           locality='San Diego',
                           state='CA',
                           **kwargs):
    """Geocoding function using Google geocoding API."""
    address_line = str(address_line)
    locality = str(locality)
    state = str(state)
    google_token = conf['google_token']
    url = 'https://maps.googleapis.com/maps/api/geocode/json?'\
          + 'address={address}&'\
          + 'components=country:US|'\
          + 'administrative_area:{state}|'\
          + 'locality:{locality}&'\
          + 'key={google_token}'

    url = url.format(address=address_line,
                     state=state,
                     locality=locality,
                     google_token=google_token)

    logging.info('Google Geocoding for: ' + address_line)
    if address_line in ['None', '', 'NaN', 'nan']:
        logging.info('No geocode for: ' + address_line)
        return None, None
    else:
        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            body = json.loads(r.content)
            candidates = body['results']
            if candidates == []:
                logging.info('No geocode for: ' + address_line)
                return None, None
            else:
                coords = body['results'][0]['geometry']['location']
                lat = coords['lat']
                lon = coords['lng']
                logging.info('Geocode success for: ' + address_line)
                return lat, lon
        except Exception, e:
            logging.error(e)
            return None, None


def reverse_geocode_google(lat='', lon='', **kwargs):
    """Reverse geocoding function using Google geocoding API."""
    google_token = conf['google_token']
    lat = str(lat)
    lon = str(lon)
    url = 'https://maps.googleapis.com/maps/api/geocode/json?'\
          + 'latlng={lat},{lon}&key={google_token}'
    url = url.format(lat=lat, lon=lon, google_token=google_token)
    if (lat == 'None' or lat == '' or lon == 'None' or lon == ''):
        logging.info('No reverse geocode for this entry')
        return None
    else:
        try:
            r = requests.get(url)
            r.raise_for_status()
            body = json.loads(r.content)
            candidates = body['results']
            if candidates == []:
                logging.info('No reverse geocode for: ' + lat + ', ' + lon)
                return None
            else:
                if 'formatted_address' not in candidates[0]:
                    logging.info('No reverse geocode for: ' + lat + ', ' + lon)
                    return None
                else:
                    address = candidates[0]['formatted_address']
                    logging.info('Geocode success for: ' + lat + ', ' + lon)
                    return address
        except Exception, e:
            logging.error(e)
            return None


def geocode_address_esri(address_line='', **kwargs):
    """Geocoding function using SANDAG geocoder."""
    # Type safe
    address_line = str(address_line)
    url = "http://gis1.sandag.org/sdgis/rest/services/REDI/REDI_COMPOSITE_LOC/GeocodeServer/findAddressCandidates"
    payload = {
        'City': 'San Diego',
        'SingleLine': address_line,
        'outSR': '4326',
        'f': 'pjson'
    }
    logging.info('ESRI Geocoding for: ' + address_line)
    if (address_line == 'None' or address_line == ''):
        logging.info('No geocode for: ' + address_line)
        return None, None
    else:
        r = requests.get(url, payload)
        r.raise_for_status()
        resp = r.json()
        candidates = resp['candidates']
        if candidates == []:
            logging.info('No geocode for: ' + address_line)
            return None, None
        else:
            logging.info('Geocode success for: ' + address_line)
            return candidates[0]['location']['y'],
            candidates[0]['location']['x']


def df_to_geodf_pt(df, lat='lat', lon='lon'):
    """Convert a dataframe with lat/lon (points) to a Geodataframe."""
    logging.info('Converting points df to geodf.')
    df = df[np.isfinite(df[lat])]
    df = df[np.isfinite(df[lon])]
    df = df[df[lat] != 0]
    df = df[df[lon] != 0]
    df['geometry'] = df.apply(lambda z: Point(z[lon], z[lat]), axis=1)
    gdf = gpd.GeoDataFrame(df)
    logging.info('Successfully created a geodf from points df.')
    return gdf


def geojson_to_geodf(file):
    """Open a geojson file and turn it into a GeodataFrame."""
    logging.info('Importing geojson file as geodf.')
    gdf = gpd.read_file(file)
    logging.info('Successfully imported geojson file as geodf.')
    return gdf


def spatial_join_pt(pt_file, poly_file, lat='lat', lon='lon'):
    """Spatially join polygon attributes to point data.

    'pt_file' is a csv file with latitude and longitude attributes that
    can be interpreted as points.

    'poly_file' is a geojson file that contains polygon data.

    lat --> latitude field in the point df
    lon --> longitude field in the point df

    Both layers must use the same CRS.

    This function returns a DataFrame, not a Geodataframe.
    """
    logging.info('Loading both layers.')
    df = pd.read_csv(pt_file)
    pt = df_to_geodf_pt(df, lat, lon)
    poly = geojson_to_geodf(poly_file)
    pt.crs = poly.crs
    logging.info('Operating spatial join.')
    pt_join = sjoin(pt, poly, how='left')
    pt_join = pt_join.drop(['geometry', 'index_right'], axis=1)
    logging.info('Successfully spatially joined data.')
    return pt_join


def extract_sde_data(table, where=''):
    """Extract table from SDE and return dataframe.

    'table': table name in SDE - what comes after 'SDW.CITY'.

    'where': where clause to refine results (e.g County scale datasets).

    """
    sde_server = conf['sde_server']
    sde_user = conf['sde_user']
    sde_pw = conf['sde_pw']

    sde_conn = pymssql.connect(sde_server, sde_user, sde_pw, 'sdw')

    if where == '':
        query = "SELECT *, [Shape].STAsText() as geom FROM SDW.CITY.{table}"
        query = query.format(table=table)

    else:
        query = "SELECT *, [Shape].STAsText() as geom FROM SDW.CITY.{table}" \
                + " WHERE {where}"
        query = query.format(table=table, where=where)

    df = pd.read_sql(query, sde_conn)

    df.columns = [x.lower() for x in df.columns]
    df = df.drop('shape', 1)

    return df


def df2shp(df, folder, layername, dtypes, gtype, epsg):
    """Convert a df extracted from SDE to a shapefile.

    'df' is a dataframe extracted from SDE with 'extract_sde_data'.

    'folder' is the path to the folder where the shapefile will be saved.

    'layername' is the name of the shapefile.

    'dtypes' is an Orderdict containing the dtypes for each field.

    'gtype' is the geometry type.

    'epsg' is the EPSG code of the output.

    """
    schema = {'geometry': gtype, 'properties': dtypes}

    with fiona.collection(
        folder + '/' + layername + '.shp',
        'w',
        driver='ESRI Shapefile',
        crs=crs.from_epsg(epsg),
        schema=schema
    ) as shpfile:
        for index, row in df.iterrows():
            geometry = loads(row['geom'])
            props = {}
            for prop in dtypes:
                props[prop] = row[prop]
            shpfile.write({'properties': props, 'geometry': mapping(geometry)})

    return 'Extracted {layername} shapefile.'.format(layername=layername)


def shp2geojson(layer):
    """Shapefile to Geojson conversion using mapshaper."""
    cmd = 'mapshaper {layer}.shp'\
        + ' -proj wgs84'\
        + ' -o format=geojson precision=0.00000001'\
        + ' {layer}.geojson'

    cmd = cmd.format(layer=layer)

    return cmd


def shp2topojson(layer):
    """Shapefile to TopoJSON conversion using mapshaper."""
    cmd = 'mapshaper {layer}.shp'\
        + ' -proj wgs84'\
        + ' -o format=topojson precision=0.00000001'\
        + ' {layer}.topojson'

    cmd = cmd.format(layer=layer)

    return cmd


def geojson2geobuf(layer):
    """Geojson to Geobuf conversion."""
    with open('{layer}.geojson'.format(layer=layer), 'r') as json:
        with open('{layer}.pbf'.format(layer=layer), 'wb') as buf:
            data = geojson.load(json)
            pbf = geobuf.encode(data)
            buf.write(pbf)
    return 'Successfully wrote geobuf.'


def geobuf2gzip(layername):
    """Gzip geobuf file."""
    os.chdir(conf['prod_data_dir'])
    with open('{layername}.pbf'.format(layername=layername), 'rb') as f_in:
        with gzip.open('{layername}.pbf.gz'.format(layername=layername), 'wb') as f_out:
            shutil.copyfileobj(f_in, f_out)
    os.rename('{layername}.pbf.gz'.format(layername=layername),
              '{layername}.pbf'.format(layername=layername))
    os.chdir(conf['home_dir'])
    return 'Sucessfully gzipped geobuf file.'


def shp2zip(layername):
    """Transfer shapefile component files to .zip archive."""
    os.chdir(conf['prod_data_dir'])
    list_files = [
        '{layername}.shp'.format(layername=layername),
        '{layername}.shx'.format(layername=layername),
        '{layername}.dbf'.format(layername=layername),
        '{layername}.prj'.format(layername=layername),
        '{layername}.cpg'.format(layername=layername)
    ]

    with zipfile.ZipFile('{layername}.zip'.format(layername=layername), 'w') as zipfolder:
        for file in list_files:
            zipfolder.write(file)
            os.remove(file)

    os.chdir(conf['home_dir'])

    return 'Successfully zipped shapefile.'


def pt_proj_conversion(lon, lat, in_proj=2230, out_proj=4326):
    """Convert a set of coordinates from one projection system to another."""
    source = osr.SpatialReference()
    source.ImportFromEPSG(in_proj)

    target = osr.SpatialReference()
    target.ImportFromEPSG(out_proj)

    transform = osr.CoordinateTransformation(source, target)

    point = ogr.CreateGeometryFromWkt("POINT ({lon} {lat})".format(lon=lon,
                                                                   lat=lat))

    point.Transform(transform)

    lat_t = point.GetY()
    lon_t = point.GetX()

    return lon_t, lat_t