# Author(s): Dr. Patrick Lemoine

import webview
import requests
import json
import socket
import os
import sys
from geopy.geocoders import Nominatim


try:
    import overpy
    OVERPY_AVAILABLE = True
except ImportError:
    overpy = None
    OVERPY_AVAILABLE = False

def internet_connection_1(url="https://www.google.com", timeout=5):
    try:
        _ = requests.get(url, timeout=timeout)
        return True
    except requests.ConnectionError:
        return False

def internet_connection_2(host="8.8.8.8", port=53, timeout=3):
    try:
        socket.setdefaulttimeout(timeout)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect((host, port)).close()
        return True
    except socket.error:
        return False

def get_wikidata_population(city, country=None):
    try:
        url_search = "https://www.wikidata.org/w/api.php"
        params = {
            'action': 'wbsearchentities',
            'search': city,
            'language': 'en',
            'format': 'json',
            'type': 'item'
        }
        resp = requests.get(url_search, params=params, timeout=6)
        results = resp.json().get('search', [])
        entity_id = None
        for result in results:
            if any(x in result['description'].lower() for x in ['commune', 'city', 'municipality']):
                entity_id = result['id']
                break
        if not entity_id and results:
            entity_id = results[0]['id']
        if not entity_id:
            return "-"
        url_entity = f"https://www.wikidata.org/wiki/Special:EntityData/{entity_id}.json"
        edata = requests.get(url_entity, timeout=8).json()
        claims = edata.get("entities", {}).get(entity_id, {}).get("claims", {})
        if "P1082" in claims:
            latest = None
            latest_year = None
            for p in claims["P1082"]:
                v = p.get("mainsnak", {}).get("datavalue", {}).get("value", {})
                if "amount" in v:
                    pop_str = v["amount"].lstrip('+')
                    pop = str(int(float(pop_str)))
                    year = "-"
                    if "qualifiers" in p and "P585" in p["qualifiers"]:
                        time_str = p["qualifiers"]["P585"][0]["datavalue"]["value"]["time"]
                        if time_str.startswith("+"):
                            year = time_str[1:5]
                    if not latest or (year != "-" and int(year) > int(latest_year or 0)):
                        latest = pop
                        latest_year = year
            return latest if latest else "-"
        return "-"
    except Exception:
        return "-"

def get_city_infos(city):
    geolocator = Nominatim(user_agent="py-maptiler-webview")
    loc = geolocator.geocode(city, exactly_one=True, addressdetails=True)
    if not loc:
        loc = geolocator.geocode("New York", addressdetails=True)
    lat, lon = float(loc.latitude), float(loc.longitude)
    address = loc.raw['address']
    country = address.get('country', '?')
    region = address.get('state', address.get('region', '?'))
    population = get_wikidata_population(city, country)
    if population == "-":
        try:
            geonames_resp = requests.get(f"http://api.geonames.org/searchJSON?q={city}&maxRows=1&username=demo")
            population = geonames_resp.json().get('geonames', [{}])[0].get('population', '-')
        except Exception:
            population = "-"
    try:
        weather_resp = requests.get(f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true")
        weather = weather_resp.json()['current_weather']
        temp = weather.get('temperature', '-')
        wind = weather.get('windspeed', '-')
    except Exception:
        temp, wind = "-", "-"
    return {
        'lat': lat, 'lon': lon,
        'country': country, 'region': region,
        'population': population, 'temp': temp, 'wind': wind, 'city': city
    }


def export_osm_buildings(city="Paris", output="buildings_cache.geojson", d=0.045):
    url_nom = f"https://nominatim.openstreetmap.org/search?q={city}&format=json"
    headers = {'User-Agent': 'Mozilla/5.0 (compatible; PyMapTilerWebview/1.0)'}
    resp_nom_raw = requests.get(url_nom, headers=headers)
    if resp_nom_raw.status_code != 200:
        raise Exception(f"Nominatim error {resp_nom_raw.status_code}: {resp_nom_raw.text[:200]}")
    resp_nom = resp_nom_raw.json()
    if not resp_nom:
        raise Exception(f"City {city} not found or no data found!")
    lat, lon = float(resp_nom[0]["lat"]), float(resp_nom[0]["lon"])
    if not OVERPY_AVAILABLE:
        geojson = {"type": "FeatureCollection", "features": []}
        count = 0
    else:
        bbox = f"{lat-d},{lon-d},{lat+d},{lon+d}"
        query = f"""
        [out:json][timeout:60];
        (
          way["building"]({bbox});
          relation["building"]({bbox});
        );
        out body;
        >;
        out skel qt;
        """
        try:
            api = overpy.Overpass()
            result = api.query(query)
            geojson = {"type": "FeatureCollection", "features": []}
            for way in result.ways:
                coords = [(float(node.lon), float(node.lat)) for node in way.nodes]
                if coords and coords[0] != coords[-1]:
                    coords.append(coords[0])
                props = dict(way.tags)

                info_keys = [
                    'name', 'building', 'building:levels', 'height',
                    'roof:shape', 'roof:material', 'roof:height',
                    'addr:street', 'addr:housenumber', 'addr:postcode', 'addr:city',
                    'start_date', 'amenity', 'shop', 'office', 'industrial',
                    'website', 'brand', 'condition', 'surface', 'source'
                ]
                properties_dict = {k: props.get(k, None) for k in info_keys if k in props}

                for k, v in props.items():
                    if k not in properties_dict:
                        properties_dict[k] = v
                geojson["features"].append({
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": [coords]},
                    "properties": properties_dict
                })
            count = len(geojson['features'])
        except Exception as ex:
            raise Exception(f"Overpass error: {ex}")
    with open(output, "w", encoding="utf-8") as f:
        json.dump(geojson, f)
    print(f"Buildings saved to {output} ({count} buildings)")
    return lat, lon


# --- HTML TEMPLATE ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>3D MapTiler/OSM Map + Satellite Terrain – {city}</title>
  <script src="https://cdn.maptiler.com/maptiler-sdk-js/v3.8.0/maptiler-sdk.umd.min.js"></script>
  <link href="https://cdn.maptiler.com/maptiler-sdk-js/v3.8.0/maptiler-sdk.css" rel="stylesheet" />
  <style>
    html, body {{ height:100%; margin:0; padding:0; }}
    #map {{ position:absolute; top:0; bottom:0; width:100%; }}
    .bar {{
      position: absolute; top: 10px; left: 10px; z-index: 10;
      background: #fff; font-family:sans-serif; padding: 13px 18px 18px 16px; border-radius: 14px;
      box-shadow: 0 2px 12px #888; min-width: 280px;
    }}
    .info {{
      position: absolute; top: 138px; left: 10px; z-index: 10; background: #f9f9f9;
      font-size:14px; padding:7px 15px; border-radius:7px; box-shadow:0 1px 7px #bbb;
      width:310px; display: none;
    }}
    #coords:empty {{ display: none; }}
    .slider-container {{ display: flex; align-items: center; margin-top: 8px; width: 90%; }}
    .slider-label {{ font-size: 14px; margin-right:12px; }}
    .slider {{ -webkit-appearance:none;width:110px;height:11px;background:#e0e0e0;border-radius:5px;outline:none;margin-right:10px;box-shadow:0 1px 3px #bbbbbb; }}
    .slider::-webkit-slider-thumb {{ -webkit-appearance:none;appearance:none;width:18px;height:18px;border-radius:50%;background:#2383e2;cursor:pointer;box-shadow:0 2px 5px #888;border:2px solid #f4f4f4; }}
    .slider::-moz-range-thumb {{ width:18px;height:18px;border-radius:50%;background:#2383e2;cursor:pointer;box-shadow:0 2px 5px #888;border:2px solid #f4f4f4; }}
    .slider-value {{ font-size:15px;margin-top:2px;min-width:30px;text-align:center;color:#2383e2;font-weight:bold;background:#f4f4f4;border-radius:5px;padding:2px 7px; }}
    #toggleTerrain {{ margin-top:12px; padding:6px 12px; font-size:14px; cursor:pointer; }}
    .maptilersdk-popup-content {{ font-family:sans-serif; font-size: 14px; }}
  </style>
</head>
<body>
  <div class="bar">
    <b>City: {city}</b><br/>
    <small>Coordinates: {lat:.5f}, {lon:.5f}</small><br>
    <small>Country: <b>{country}</b>, Region: <b>{region}</b></small><br>
    <small>Population: <b>{population}</b></small><br>
    <small>Weather: <b>{temp}°C</b> / Wind: <b>{wind} km/h</b></small>
    <br><label for="style">Style: </label>
    <select id="style">
      <option value="DEFAULT">Default</option>
      <option value="BASIC">Basic</option>
      <option value="STREETS">Streets</option>
      <option value="WINTER">Winter</option>
      <option value="SATELLITE">Satellite</option>
      <option value="DARK">Dark</option>
    </select>
    <span id="zoominfo"></span>
    <div class="slider-container">
      <span class="slider-label">Building Opacity:</span>
      <input type="range" min="0" max="1" step="0.05" value="0.4" id="opacity" class="slider">
      <span class="slider-value" id="opacity_val">0.4</span>
    </div>
    <div class="slider-container">
      <span class="slider-label">Satellite Opacity:</span>
      <input type="range" min="0" max="1" step="0.05" value="0.55" id="opac_sat" class="slider">
      <span class="slider-value" id="opac_satval">0.55</span>
    </div>
    <button id="toggleTerrain">Disable Terrain Relief</button>
  </div>
  <div class="info" id="coords"></div>
  <div id="map"></div>
  <script>
    maptilersdk.config.apiKey = '{api_key}';
    let currentPopup;
    let styleList = {{
      'BASIC': maptilersdk.MapStyle.BASIC,
      'STREETS': maptilersdk.MapStyle.STREETS,
      'WINTER': maptilersdk.MapStyle.WINTER,
      'SATELLITE': maptilersdk.MapStyle.SATELLITE,
      'DARK': maptilersdk.MapStyle.DARK,
    }};
    let map = new maptilersdk.Map({{
      container: "map",
      style: styleList.STREETS,
      center: [ {lon}, {lat} ],
      zoom: 13,
      pitch: 45,
      bearing: -17.6,
      enableTerrain: true
    }});
    map.addControl(new maptilersdk.NavigationControl(), 'top-right');
    let initial = {{
      style: styleList.STREETS,
      center: [ {lon}, {lat} ],
      zoom: 13,
      pitch: 45,
      bearing: -17.6,
      opacity: "0.4",
      opac_sat: "0.55"
    }};
    let terrainEnabled = true;

    function setBothBuildingsOpacity(val) {{
      if (map.getLayer('Building 3D')) {{
        map.setPaintProperty('Building 3D', 'fill-extrusion-opacity', parseFloat(val));
      }}
      if (map.getLayer('osm_buildings_layer')) {{
        map.setPaintProperty('osm_buildings_layer', 'fill-extrusion-opacity', parseFloat(val));
      }}
    }}
    function setSatOpacity(val) {{
      if (map.getLayer('satellite_layer')) {{
        map.setPaintProperty('satellite_layer', 'raster-opacity', parseFloat(val));
      }}
    }}
    document.getElementById("opacity").oninput = function(e) {{
      let val = e.target.value;
      document.getElementById("opacity_val").textContent = val;
      setBothBuildingsOpacity(val);
    }};
    document.getElementById("opac_sat").oninput = function(e) {{
      let val = e.target.value;
      document.getElementById("opac_satval").textContent = val;
      setSatOpacity(val);
    }};
    document.getElementById("style").onchange = function(e) {{
      let val = e.target.value;
      if (val === "DEFAULT") {{
        map.setStyle(initial.style);
        map.setCenter(initial.center);
        map.setZoom(initial.zoom);
        map.setPitch(initial.pitch);
        map.setBearing(initial.bearing);
        document.getElementById("opacity").value = initial.opacity;
        document.getElementById("opacity_val").textContent = initial.opacity;
        setBothBuildingsOpacity(initial.opacity);
        document.getElementById("opac_sat").value = initial.opac_sat;
        document.getElementById("opac_satval").textContent = initial.opac_sat;
        setSatOpacity(initial.opac_sat);
      }} else {{
        map.setStyle(styleList[val]);
      }}
    }};
    document.getElementById("toggleTerrain").onclick = function() {{
      if (terrainEnabled) {{
        map.setTerrain(null);
        terrainEnabled = false;
        document.getElementById("toggleTerrain").textContent = "Enable Terrain Relief";
      }} else {{
        if (!map.getSource('terrain')) {{
          map.addSource('terrain', {{
            type: 'raster-dem',
            url: 'https://api.maptiler.com/tiles/terrain-rgb/tiles.json?key={api_key}',
            tileSize: 256,
            maxzoom: 12
          }});
        }}
        map.setTerrain({{source: 'terrain', exaggeration: 1.0}});
        terrainEnabled = true;
        document.getElementById("toggleTerrain").textContent = "Disable Terrain Relief";
      }}
    }};
    map.on('load', function () {{
      map.addSource('terrain', {{
        type: 'raster-dem',
        url: 'https://api.maptiler.com/tiles/terrain-rgb/tiles.json?key={api_key}',
        tileSize: 256,
        maxzoom: 12
      }});
      map.setTerrain({{source: 'terrain', exaggeration: 1.0}});
      document.getElementById("toggleTerrain").textContent = "Disable Terrain Relief";
      terrainEnabled = true;
      map.addSource('satellite', {{
        type: 'raster',
        tiles: [
          'https://api.maptiler.com/tiles/satellite/{{z}}/{{x}}/{{y}}.jpg?key={api_key}'
        ],
        tileSize: 256
      }});
      map.addLayer({{
        id: 'satellite_layer',
        type: 'raster',
        source: 'satellite',
        paint: {{
          'raster-opacity': parseFloat(document.getElementById("opac_sat").value)
        }}
      }});
      const layers = map.getStyle().layers;
      let labelLayerId;
      for (let i = 0; i < layers.length; i++) {{
        if (layers[i].type === 'symbol' && layers[i].layout['text-field']) {{
          labelLayerId = layers[i].id;
          break;
        }}
      }}
      map.addLayer({{
        "id": "Building 3D",
        "source": "maptiler_planet",
        "type": "fill-extrusion",
        "source-layer": "building",
        "filter": ["!has", "hide_3d"],
        "minzoom": 14,
        "paint": {{
          "fill-extrusion-base": {{"property": "render_min_height", "type": "identity"}},
          "fill-extrusion-color": "hsl(44,14%,79%)",
          "fill-extrusion-height": {{"property": "render_height", "type": "identity"}},
          "fill-extrusion-opacity": parseFloat(document.getElementById("opacity").value)
        }}
      }}, labelLayerId);
      setBothBuildingsOpacity(document.getElementById("opacity").value);

      fetch('{geojson_cache_filename}')
        .then(function(response) {{
          if (!response.ok) {{
            console.error('GeoJSON loading error: ' + response.statusText + ' for ' + '{geojson_cache_filename}');
            return {{ "type": "FeatureCollection", "features": [] }};
          }}
          return response.json();
        }})
        .then(function(geojson) {{
          if (!geojson || geojson.features.length === 0) {{
            console.warn("Loaded GeoJSON is empty or invalid.");
            return;
          }}
          map.addSource('osm_buildings', {{ type: 'geojson', data: geojson }});
          map.addLayer({{
            id: 'osm_buildings_layer',
            type: 'fill-extrusion',
            source: 'osm_buildings',
            paint: {{
              'fill-extrusion-color': '#d4af37',
              'fill-extrusion-height': [
                'coalesce',
                ['to-number', ['get', 'height']],
                30
              ],
              'fill-extrusion-opacity': parseFloat(document.getElementById("opacity").value)
            }}
          }});
          setBothBuildingsOpacity(document.getElementById("opacity").value);
        }})
        .catch(function(error) {{
          console.error("Critical GeoJSON fetch error:", error);
        }});

      new maptilersdk.Marker().setLngLat([ {lon}, {lat} ]).addTo(map);

      map.on('move', function() {{
        let z = map.getZoom().toFixed(2);
        let pitch = map.getPitch().toFixed(1);
        let bearing = map.getBearing().toFixed(1);
        document.getElementById('zoominfo').innerHTML =
          '<br/>Zoom: ' + z + ' / Pitch: ' + pitch + ' / Bearing: ' + bearing;
      }});

      map.on('click', function(e) {{
        var buildingFeatures = map.queryRenderedFeatures(e.point, {{
          layers: ['Building 3D', 'osm_buildings_layer']
        }});
                
        if (typeof currentPopup !== 'undefined' && currentPopup) {{
          currentPopup.remove();
          currentPopup = null;
        }}
        if (buildingFeatures.length > 0) {{
          let props = buildingFeatures[0].properties;
          
         
          let coords = e.lngLat;
          let infoHtml = "<h4>" + (props.name || props.amenity || props.building || "Building") + "</h4>";
          let height_val = props.height || props.render_height;
          if (height_val) {{
            try {{ height_val = parseFloat(height_val).toFixed(1); }} catch (e) {{}}
            infoHtml += "<b>Height:</b> " + height_val + (props.height ? "m" : "m (estimated)") + "<br/>";
          }}
          if (props['building:levels']) {{
            infoHtml += "<b>Floors:</b> " + props['building:levels'] + "<br/>";
          }}
          if (props.building) {{
            infoHtml += "<b>Type:</b> " + props.building + "<br/>";
          }} else if (props.amenity) {{
            infoHtml += "<b>Usage:</b> " + props.amenity + "<br/>";
          }}

          //alert(props);
              
          if (props["addr:street"]) {{
              infoHtml += "<b>Adresse:</b> " + props["addr:street"];
              if (props["addr:housenumber"]) infoHtml += " " + props["addr:housenumber"];
              if (props["addr:postcode"]) infoHtml += ", " + props["addr:postcode"];
              if (props["addr:city"]) infoHtml += " " + props["addr:city"];
              infoHtml += "<br/>";
           }}    
              
              
              
              
          currentPopup = new maptilersdk.Popup({{offset: 25}})
            .setLngLat(coords)
            .setHTML(infoHtml)
            .addTo(map);
        }} else {{
          var roadFeatures = map.queryRenderedFeatures(e.point, {{
            layers: ['road', 'transportation', 'transportation-name']
          }});
          let roadName = "";
          if (roadFeatures.length > 0) {{
            roadName = roadFeatures[0].properties.name || roadFeatures[0].properties['name:en'] || "";
            if (roadName) {{
              currentPopup = new maptilersdk.Popup({{offset: 25}})
                .setLngLat(e.lngLat)
                .setHTML("<b>Road:</b> " + roadName)
                .addTo(map);
              return;
            }}
          }}
        }}
      }});
    }});
  </script>
</body>
</html>
"""

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--Path', type=str, default='.', help='Path.')
    parser.add_argument('--API_KEY', type=str, default='', help='API_KEY.')
    parser.add_argument('--City', type=str, default="New York", help='City Name.')
    parser.add_argument('--AskCity', action='store_true', help='Tkinter dialog to enter city name')
    parser.add_argument('--ForceOSM', action='store_true', help='Force extraction/save of OSM cache at each launch')
    args = parser.parse_args()

    MAPTILER_API_KEY = args.API_KEY

    city = args.City
    
    if args.AskCity:
        import tkinter as tk
        from tkinter.simpledialog import askstring
        root = tk.Tk()
        root.withdraw()
        city = askstring("City", "Enter the name of the city to display:", initialvalue=city)
        if not city:
            sys.exit(0)
    
    if not (internet_connection_1() or internet_connection_2()):
        print("No Internet Connection !!!")
        sys.exit(1)
    
    if not os.path.exists(args.Path):
        os.makedirs(args.Path)
    
    d_box = 0.02
    geojson_cache_filename = "buildings_cache.geojson"
    geojson_cache_path = os.path.join(args.Path, geojson_cache_filename)
    
    should_extract = args.ForceOSM or not os.path.exists(geojson_cache_path) or (os.path.exists(geojson_cache_path) and os.stat(geojson_cache_path).st_size == 0)
    if should_extract:
        if OVERPY_AVAILABLE:
            print("Extracting OSM buildings cache…")
            try:
                export_osm_buildings(city=city, output=geojson_cache_path, d=d_box)
            except Exception as e:
                print(f"OSM extraction failed: {e}")
        else:
            print("WARNING: Extraction skipped because the 'overpy' module is not installed.")
    if not os.path.exists(geojson_cache_path) or os.stat(geojson_cache_path).st_size == 0:
        print(f"Creating empty GeoJSON cache file: {geojson_cache_path}.")
        with open(geojson_cache_path, "w", encoding="utf-8") as f:
            json.dump({"type": "FeatureCollection", "features": []}, f)
    
    print(f"OSM cache found: {geojson_cache_path}. Preparing for browser loading.")
    infos = get_city_infos(city)
    html_content = HTML_TEMPLATE.format(
        api_key=MAPTILER_API_KEY,
        geojson_cache_filename=geojson_cache_filename,
        **infos
    )
    html_temp_filename = "temp_map_viewer.html"
    html_temp_path = os.path.join(args.Path, html_temp_filename)
    with open(html_temp_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"Temporary HTML file created: {html_temp_path}.")
    
    try:
        os.chdir(args.Path)
        print(f"Changed working directory to: {os.getcwd()}")
        webview.create_window(
            f"3D MapTiler/OSM Map + Satellite Terrain – {city}",
            url=html_temp_path,
            width=1200,
            height=890
        )
        webview.start()
    except Exception as e:
        print(f"Error when starting webview: {e}")


   