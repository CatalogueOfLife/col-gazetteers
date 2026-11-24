function import_political() {
    echo "Downloading Political (Marine Regions Land Union) dataset"

    # EEZ + land union (we're currently on version 4, which uses EEZ version 12):
    # Download from here: http://www.marineregions.org/downloads.php

    mkdir -p $TEMP
    cd $TEMP
    curl -LSs --remote-name --continue-at - --fail https://download.gbif.org/MapDataMirror/2026/10/EEZ_land_union_v4_202410.zip
    mkdir -p political
    unzip -oj EEZ_land_union_v4_202410.zip -d political/

    shp2pgsql -d -D -s 4326 -i -I -W UTF-8 political/EEZ_land_union_v4_202410.shp public.political | wrap_drop_geometry_commands > political/political.sql

    echo "Dropping old tables"
    echo "DROP TABLE IF EXISTS political;" | exec_psql

    echo "Importing Marine Regions to PostGIS"
    exec_psql_file political/political.sql

    rm EEZ_land_union_v4_202410.zip political/ -Rf

    echo "DROP TABLE IF EXISTS osm;" | exec_psql

    echo "Importing OSM from $START_DIR/osm"
    cd $START_DIR/osm
    for id in 3777250 2088990 52411 913110 1867188 1650407; do
        [[ -f $id.xml ]] || curl -Ssfo $id.xml https://api.openstreetmap.org/api/0.6/relation/$id/full
        ogr2ogr -append -f PostgreSQL "$PGCONN dbname=$POSTGRES_DB" $id.xml -nln osm -lco GEOMETRY_NAME=geom multipolygons
    done

    # Add the code for Antarctica (missing since Antarctica doesn't have sovereignty etc).
    echo "UPDATE political SET iso_ter1 = 'ATA' WHERE mrgid_eez = 8489;" | exec_psql
    echo "INSERT INTO political (gid, \"union\", mrgid_eez, iso_ter1, geom) SELECT -199, 'Antarctica', 8489, 'ATA', ST_Multi(ST_SetSRID(ST_MakeBox2D(ST_Point(-180, -90), ST_Point(180, -88)), 4326));" | exec_psql

    # China / Taiwan: remove Fukien Province from China and add to Taiwan
    # 8486 / 8321 / https://www.openstreetmap.org/relation/3777250
    # https://github.com/gbif/geocode/issues/2
    # https://github.com/vlizBE/marineregions-issues/issues/135
    echo "WITH osm AS (SELECT osm.geom FROM osm WHERE osm_id = '3777250')" \
        "UPDATE political SET geom = ST_Difference(political.geom, osm.geom) FROM osm WHERE mrgid_eez = '8486';" | exec_psql
    echo "WITH osm AS (SELECT osm.geom FROM osm WHERE osm_id = '3777250')" \
        "UPDATE political SET geom = ST_Union(political.geom, osm.geom) FROM osm WHERE mrgid_eez = '8321';" | exec_psql

    # Serbia / Kosovo
    # SRB / XKX / https://www.openstreetmap.org/relation/2088990
    echo "WITH osm AS (SELECT osm.geom FROM osm WHERE osm_id = '2088990')," \
        "xkx AS (SELECT ST_Intersection(osm.geom, political.geom) AS geom FROM political, osm WHERE iso_ter1 = 'SRB')" \
        "INSERT INTO political (\"union\", territory1, iso_ter1, iso_sov1, geom) SELECT 'Kosovo', 'Kosovo', 'XKX', 'XKX', ST_Multi(geom) FROM xkx;" | exec_psql
    echo "WITH osm AS (SELECT osm.geom FROM osm WHERE osm_id = '2088990')" \
        "UPDATE political SET geom = ST_Difference(political.geom, osm.geom) FROM osm WHERE iso_ter1 = 'SRB';" | exec_psql

    # China / Hong Kong
    # CHN / HKG / https://www.openstreetmap.org/relation/913110
    echo "WITH osm AS (SELECT osm.geom FROM osm WHERE osm_id = '913110')," \
        "hkg AS (SELECT ST_Intersection(osm.geom, political.geom) AS geom FROM political, osm WHERE iso_ter1 = 'CHN')" \
        "INSERT INTO political (\"union\", territory1, iso_ter1, iso_sov1, geom) SELECT 'Hong Kong', 'Hong Kong', 'HKG', 'HKG', ST_Multi(geom) FROM hkg;" | exec_psql
    echo "WITH osm AS (SELECT osm.geom FROM osm WHERE osm_id = '913110')" \
        "UPDATE political SET geom = ST_Multi(ST_Difference(political.geom, osm.geom)) FROM osm WHERE iso_ter1 = 'CHN';" | exec_psql
    echo "DELETE FROM political WHERE iso_ter1 = 'HKG' AND ST_NPoints(geom) = 0;" | exec_psql

    # China / Macao
    # CHN / MAC / https://www.openstreetmap.org/relation/1867188
    echo "WITH osm AS (SELECT osm.geom FROM osm WHERE osm_id = '1867188')," \
        "mac AS (SELECT ST_Intersection(osm.geom, political.geom) AS geom FROM political, osm WHERE iso_ter1 = 'CHN')" \
        "INSERT INTO political (\"union\", territory1, iso_ter1, iso_sov1, geom) SELECT 'Macao', 'Macao', 'MAC', 'MAC', ST_Multi(geom) FROM mac;" | exec_psql
    echo "WITH osm AS (SELECT osm.geom FROM osm WHERE osm_id = '1867188')" \
        "UPDATE political SET geom = ST_Multi(ST_Difference(political.geom, osm.geom)) FROM osm WHERE iso_ter1 = 'CHN';" | exec_psql
    echo "DELETE FROM political WHERE iso_ter1 = 'MAC' AND ST_NPoints(geom) = 0;" | exec_psql

    # Netherlands / Belgium water bit
    # NLD / BEL / https://www.openstreetmap.org/relation/52411
    echo "WITH bbox AS (SELECT ST_GeomFromEWKT('SRID=4326;POLYGON((4.15 51.008,4.15 51.408,4.439 51.408,4.439 51.008,4.15 51.008))') AS geom)," \
        "osm AS (SELECT ST_Intersection(osm.geom, bbox.geom) AS geom FROM bbox, osm WHERE osm_id = '52411')" \
        "UPDATE political SET geom = ST_Union(political.geom, osm.geom) FROM osm WHERE iso_ter1 = 'BEL';" | exec_psql
    echo "WITH bbox AS (SELECT ST_GeomFromEWKT('SRID=4326;POLYGON((4.15 51.008,4.15 51.408,4.439 51.408,4.439 51.008,4.15 51.008))') AS geom)," \
        "osm AS (SELECT ST_Intersection(osm.geom, bbox.geom) AS geom FROM bbox, osm WHERE osm_id = '52411')" \
        "UPDATE political SET geom = ST_Multi(ST_Difference(political.geom, osm.geom)) FROM osm WHERE iso_ter1 = 'NLD';" | exec_psql

    # Finland / Åland Islands
    # FIN / ALA / https://www.openstreetmap.org/relation/1650407
    echo "WITH osm AS (SELECT osm.geom FROM osm WHERE osm_id = '1650407')," \
        "ala AS (SELECT ST_Intersection(osm.geom, political.geom) AS geom FROM political, osm WHERE iso_ter1 = 'FIN')" \
        "INSERT INTO political (\"union\", territory1, iso_ter1, iso_sov1, geom) SELECT 'Åland Islands', 'Åland Islands', 'ALA', 'ALA', ST_Multi(geom) FROM ala;" | exec_psql
    echo "WITH osm AS (SELECT osm.geom FROM osm WHERE osm_id = '1650407')" \
        "UPDATE political SET geom = ST_Difference(political.geom, osm.geom) FROM osm WHERE iso_ter1 = 'FIN';" | exec_psql

    # United States / United States Minor Outlying Islands
    # https://en.wikipedia.org/wiki/ISO_3166-2:UM
    # Remove Midway Atoll (United States Minor Outlying Islands) from Hawaii
    echo "WITH handdrawn AS (SELECT ST_GeomFromEWKT('SRID=4326;POLYGON ((-177.04996373497760942 31.60978833477749461, -177.83691262724664739 24.87185288052087273, -177.66332700965642744 24.37901220724413065, -177.36250316477767797 24.6621005824437951, -175.72778425823611315 31.30369873179363793, -176.27520866730711191 31.83088711396169757, -177.04996373497760942 31.60978833477749461))') AS geom)," \
        "midway AS (SELECT ST_Intersection(handdrawn.geom, political.geom) AS geom FROM political, handdrawn WHERE mrgid_eez = 8453)" \
        "INSERT INTO political (\"union\", territory1, iso_ter1, iso_sov1, geom) SELECT 'Midway Atoll', 'Midway Atoll', 'UMI', 'UMI', ST_Multi(geom) FROM midway;" | exec_psql
    echo "WITH handdrawn AS (SELECT ST_GeomFromEWKT('SRID=4326;POLYGON ((-177.04996373497760942 31.60978833477749461, -177.83691262724664739 24.87185288052087273, -177.66332700965642744 24.37901220724413065, -177.36250316477767797 24.6621005824437951, -175.72778425823611315 31.30369873179363793, -176.27520866730711191 31.83088711396169757, -177.04996373497760942 31.60978833477749461))') AS geom)" \
        "UPDATE political SET geom = ST_Difference(political.geom, handdrawn.geom) FROM handdrawn WHERE mrgid_eez = 8453;" | exec_psql
    # Set Navassa Island to United States Minor Outlying Islands
    echo "UPDATE political SET iso_ter2 = 'UMI', iso_ter3 = 'JAM' WHERE mrgid_eez = 48951;" | exec_psql

    # Remove Overlapping Claim South China Sea, MR DB doesn't mark a sovereign claim here (only case of this).
    echo "DELETE from political WHERE mrgid_eez = 49003;" | exec_psql

    # Delete the Joint Regime Areas that completely overlap an ordinary EEZ.
    echo "DELETE FROM political WHERE mrgid_eez IN(48961, 21795, 48970, 48968, 50167, 48974, 48969, 48973, 48975, 48976, 48962, 48964, 48966, 48967);" | exec_psql

    # Add ISO codes where they are missing
    echo "UPDATE political SET iso_ter1 = iso_sov1 WHERE iso_ter1 IS NULL;" | exec_psql
    echo "UPDATE political SET iso_ter2 = iso_sov2 WHERE iso_ter2 IS NULL;" | exec_psql
    echo "UPDATE political SET iso_ter3 = iso_sov3 WHERE iso_ter3 IS NULL;" | exec_psql

    # Add mrgid_ter1 codes where these are oddly missing
    echo "UPDATE political SET mrgid_ter1 = 17589 WHERE iso_ter1 = 'MAC' AND mrgid_ter1 IS NULL;" | exec_psql
    echo "UPDATE political SET mrgid_ter1 = 8759  WHERE iso_ter1 = 'HKG' AND mrgid_ter1 IS NULL;" | exec_psql
    echo "UPDATE political SET mrgid_ter1 = 48519 WHERE iso_ter1 = 'XKX' AND mrgid_ter1 IS NULL;" | exec_psql

    # Set ISO code for Chagos Archiplago, consistent with ISO 3166-1.
    echo "UPDATE political SET iso_ter1 = 'IOT', iso_ter2 = 'MUS' WHERE mrgid_eez = 62589;" | exec_psql

    # Use sovereign codes rather than territory codes for disputed territories
    echo "UPDATE political SET iso_ter2 = iso_sov2 WHERE iso_ter1 IN ('FLK', 'SGS', 'MYT', 'ESH');" | exec_psql

    # France considers the Tromelin Island part of the French Southern and Antarctic Lands
    echo "UPDATE political SET iso_ter1 = 'ATF' WHERE mrgid_eez = 48946;" | exec_psql
    # And the Matthew and Hunter Islands part of New Caledonia
    echo "UPDATE political SET iso_ter1 = 'NCL' WHERE mrgid_eez = 48948;" | exec_psql

    # Ukraine is marked twice
    echo "UPDATE political SET territory2 = NULL, mrgid_ter2 = NULL, iso_ter2 = NULL, un_ter2 = NULL, sovereign2 = NULL, mrgid_sov2 = NULL, iso_sov2 = NULL, un_sov2 = NULL WHERE mrgid_eez = 5695;" | exec_psql

    # Note the areas having overlapping claims of some form, particularly these which include land:
    #
    # SELECT DISTINCT "union", iso_ter1, iso_ter2, iso_ter3 FROM political, esri_countries WHERE iso_ter2 IS NOT NULL AND political.geom && esri_countries.geom AND ST_Intersects(political.geom, esri_countries.geom);
    #                            union                           │ iso_ter1 │ iso_ter2 │ iso_ter3
    # ───────────────────────────────────────────────────────────┼──────────┼──────────┼──────────
    #  Abu musa, Greater and Lesser Tunb                         │ ARE      │ IRN      │ ␀
    #  Alhucemas Islands                                         │ ESP      │ MAR      │ ␀
    #  Ceuta                                                     │ ESP      │ MAR      │ ␀
    #  Chafarinas Islands                                        │ ESP      │ MAR      │ ␀
    #  Doumeira Islands                                          │ ERI      │ DJI      │ ␀
    #  Falkland Islands                                          │ FLK      │ ARG      │ ␀
    #  Gibraltar                                                 │ GIB      │ ESP      │ ␀
    #  Glorioso Islands                                          │ MDG      │ ATF      │ ␀
    #  Hala'ib Triangle                                          │ SDN      │ EGY      │ ␀
    #  Kuril Islands                                             │ JPN      │ RUS      │ ␀
    #  Matthew and Hunter Islands                                │ NCL      │ VUT      │ ␀
    #  Mayotte                                                   │ MYT      │ COM      │ ␀
    #  Melilla                                                   │ ESP      │ MAR      │ ␀
    #  Navassa Island                                            │ HTI      │ UMI      │ JAM
    #  Overlapping claim: Kenya / Somalia                        │ KEN      │ SOM      │ ␀
    #  Palestine                                                 │ PSE      │ ISR      │ ␀
    #  Perejil Island                                            │ ESP      │ MAR      │ ␀
    #  Peñón de Vélez de la Gomera                               │ ESP      │ MAR      │ ␀
    #  Protected Zone established under the Torres Strait Treaty │ PNG      │ AUS      │ ␀
    #  Senkaku Islands                                           │ TWN      │ JPN      │ CHN
    #  South Georgia and the South Sandwich Islands              │ SGS      │ ARG      │ ␀
    #  Tromelin Island                                           │ ATF      │ MDG      │ MUS
    #  Western Sahara                                            │ ESH      │ MAR      │ ␀

    echo "SELECT AddGeometryColumn('political', 'centroid_geom', 4326, 'POINT', 2);" | exec_psql
    echo "UPDATE political SET centroid_geom = ST_Centroid(geom);" | exec_psql

    echo "CREATE INDEX political_eez_iso_3digit ON political (iso_ter1);" | exec_psql

    echo "Subdividing political layer"
    cd $START_DIR
    exec_psql_file $SCRIPT_DIR/subdivide_layer_political.sql

    echo "Marine Regions Land Union import complete"
    echo
}

function import_mr_eez() {
    echo "Downloading MR EEZ & ABNJ datasets"
    mkdir -p $TEMP
    cd $TEMP

    # EEZ v12: https://doi.org/10.14284/632
    curl -LSs --remote-name --continue-at - --fail https://download.gbif.org/MapDataMirror/2026/10/eez_v12.gpkg
    echo "Importing EEZ to PostGIS"
    ogr2ogr -overwrite -nln mr_eez -lco GEOMETRY_NAME=geom -f PostgreSQL "$PGCONN" eez_v12.gpkg
    rm eez_v12.gpkg -Rf
    echo "EEZ import complete"
    echo

    # High Seas v2: https://doi.org/10.14284/696
    curl -LSs --remote-name --continue-at - --fail https://download.gbif.org/MapDataMirror/2026/10/High_Seas_v2.gpkg
    echo "Importing High Seas to PostGIS"
    ogr2ogr -nln mr_eez -lco GEOMETRY_NAME=geom -f PostgreSQL "$PGCONN" High_Seas_v2.gpkg
    rm High_Seas_v2.gpkg -Rf
    echo "High Seas import complete"
    echo

    echo "UPDATE mr_eez SET geoname = 'High Seas' WHERE mrgid = 63203;" | exec_psql
    echo "UPDATE mr_eez SET geom = ST_MakeValid(geom) WHERE NOT ST_IsValid(geom);" | exec_psql

    # Centroids
    echo "SELECT AddGeometryColumn('mr_eez', 'centroid_geom', 4326, 'POINT', 2);" | exec_psql
    echo "UPDATE mr_eez SET centroid_geom = ST_Centroid(geom);" | exec_psql

    echo "Subdividing mr_eez layer"
    cd $START_DIR
    exec_psql_file $SCRIPT_DIR/subdivide_layer_mr_eez.sql

    echo "MR EEZ & ABNJ import complete"
}
