import os
import json
from tqdm import tqdm
from osgeo import gdal  # use gdal env for this script.


def main():
    geoimages_dir = "train/sat/"
    shapefiles_dir = "train/shape/"

    save_shapefiles_dir = "train/pixel_geojson/"
    os.makedirs(save_shapefiles_dir, exist_ok=True)

    geoimages = os.listdir(geoimages_dir)
    shapefiles = os.listdir(shapefiles_dir)

    for i in tqdm(range(len(geoimages))):
        geo_im = geoimages[i]
        # im_desc = geo_im.split('_')[-1].split('.')[0]
        im_desc = geo_im.split('.')[0]
        shp = [sh for sh in shapefiles if f"{im_desc}.geojson" in sh]
        assert len(shp) == 1
        shp = shp[0]
        # shp = shapefiles[0]

        driver = gdal.GetDriverByName('GTiff')
        dataset = gdal.Open(os.path.join(geoimages_dir, geo_im))
        band = dataset.GetRasterBand(1)
        cols = dataset.RasterXSize
        rows = dataset.RasterYSize
        transform = dataset.GetGeoTransform()
        xOrigin = transform[0]
        yOrigin = transform[3]
        pixelWidth = transform[1]
        pixelHeight = -transform[5]
        data = band.ReadAsArray(0, 0, cols, rows)

        with open(os.path.join(shapefiles_dir, shp), 'r') as f:
            geo_shp = json.load(f)

        pixel_shp = {}
        pixel_shp['type'] = geo_shp['type']
        pixel_shp['features'] = []

        for feature in geo_shp['features']:
            out_feat = {
                'type': 'Feature',
                'geometry': {
                    'type': 'MultiLineString',
                    'coordinates': []
                }
            }
            if feature['geometry']['type'] == "MultiLineString":
                feature_coords = feature['geometry']['coordinates']
                for geo_coords in feature_coords:
                    points_list = [(gc[0], gc[1]) for gc in geo_coords]
                    coords_list = []
                    for point in points_list:
                        col = (point[0] - xOrigin) / pixelWidth
                        col = col if col < cols else cols
                        row = (yOrigin - point[1]) / pixelHeight
                        row = row if row < rows else rows
                        # 'row' has negative sign to be compatible with qgis visualization. Must be removed for compatibility in image space.
                        coords_list.append([col, -row])
                    out_feat['geometry']['coordinates'].append(coords_list)
                    pixel_shp['features'].append(out_feat)
            else:
                geo_coords = feature['geometry']['coordinates']
                points_list = [(gc[0], gc[1]) for gc in geo_coords]
                coords_list = []
                for point in points_list:
                    col = (point[0] - xOrigin) / pixelWidth
                    col = col if col < cols else cols
                    row = (yOrigin - point[1]) / pixelHeight
                    row = row if row < rows else rows
                    # 'row' has negative sign to be compatible with qgis visualization. Must be removed for compatibility in image space.
                    # coords_list.append([col, -row])
                    coords_list.append([col, row])
                out_feat['geometry']['coordinates'].append(coords_list)
                pixel_shp['features'].append(out_feat)

        with open(os.path.join(save_shapefiles_dir, shp), 'w') as o:
            json.dump(pixel_shp, o)


if __name__ == "__main__":
    main()