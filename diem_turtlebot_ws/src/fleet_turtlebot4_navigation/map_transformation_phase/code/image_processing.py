# image_processing.py

import cv2
import numpy as np
from scipy.ndimage import distance_transform_edt, convolve, generic_filter
from skimage.morphology import skeletonize
import logging
from PIL import Image
import os
def load_map(image_path, negate):
    """
    Loads the occupancy map from a grayscale image file and applies negation if required.

    Parameters:
        image_path (str): Path to the image file to load.
        negate (int): If set to 1, inverts the map.

    Returns:
        numpy.ndarray: The loaded (and possibly inverted) grayscale image.
    """
    occupancy_grid = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if occupancy_grid is None:
        raise FileNotFoundError(f"Error: unable to open file {image_path}")
    
    if negate:
        occupancy_grid = cv2.bitwise_not(occupancy_grid)
        logging.info("Map negation applied.")
    
    return occupancy_grid

def clean_map(occupancy_grid, config, map_directory, map_name):
    """
    Applies morphological transformations to clean the map.

    Parameters:
        occupancy_grid (numpy.ndarray): The grayscale image of the map.
        config (Config): Configuration object with dynamic parameters.
        map_directory (str): Directory to save intermediate maps.
        map_name (str): Base name for saving maps.

    Returns:
        numpy.ndarray: The transformed map.
    """
    logging.info("Starting map cleaning process.")

    # Step 1: Morphological closing
    kernel_close_size = config.kernel_close_size
    logging.debug(f"Using kernel_close_size: {kernel_close_size}")
    kernel_close = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_close_size, kernel_close_size))
    closed_map = cv2.morphologyEx(occupancy_grid, cv2.MORPH_CLOSE, kernel_close)
    logging.debug("Applied morphological closing.")
    
    # Step 2: Dilation
    kernel_dilate_size = config.kernel_dilate_size
    logging.debug(f"Using kernel_dilate_size: {kernel_dilate_size}")
    kernel_dilate = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_dilate_size, kernel_dilate_size))
    dilated_map = cv2.dilate(closed_map, kernel_dilate, iterations=1)
    logging.debug("Applied dilation.")
    
    # Step 3: Opening
    kernel_open_size = config.kernel_open_size
    logging.debug(f"Using kernel_open_size: {kernel_open_size}")
    kernel_open = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_open_size, kernel_open_size))
    opened_map = cv2.morphologyEx(dilated_map, cv2.MORPH_OPEN, kernel_open)
    logging.debug("Applied opening.")
    
    # Step 4: Erosion
    kernel_erode_size = config.kernel_erode_size
    logging.debug(f"Using kernel_erode_size: {kernel_erode_size}")
    kernel_erode = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_erode_size, kernel_erode_size))
    final_map = cv2.erode(opened_map, kernel_erode, iterations=1)
    logging.debug("Applied erosion.")
    save_as_png(final_map, os.path.join(map_directory, f"{map_name}_final_cleaned_map.png"))
    
    # Check if final map is empty
    if not np.any(final_map):
        logging.error("Final cleaned map is empty. Please check the input map and parameters.")
        raise ValueError("Final cleaned map is empty.")
    
    logging.info("Map cleaning process completed successfully.")
    return final_map

def create_binary_map(occupancy_grid):
    """
    Converts the map image into a binary map.

    Parameters:
        occupancy_grid (numpy.ndarray): The grayscale image of the map.

    Returns:
        numpy.ndarray: The resulting binary map.
    """
    _, binary_map = cv2.threshold(occupancy_grid, 240, 255, cv2.THRESH_BINARY)
    logging.debug("Created binary map using fixed threshold.")
    return binary_map

def compute_distance_map(binary_map):
    """
    Calculates the Euclidean distance map from non-zero pixels.

    Parameters:
        binary_map (numpy.ndarray): The binary map.

    Returns:
        numpy.ndarray: The Euclidean distance map.
    """
    distance_map = distance_transform_edt(binary_map)
    logging.debug("Computed Euclidean distance map.")
    return distance_map

def create_voronoi_lines(distance_map):
    """
    Creates Voronoi lines by identifying pixels with neighboring values of different distances.

    Parameters:
        distance_map (numpy.ndarray): The Euclidean distance map.

    Returns:
        numpy.ndarray: The resulting Voronoi map.
    """
    kernel = np.array([[1, 1, 1],
                       [1, 0, 1],
                       [1, 1, 1]], dtype=bool)

    def local_range(values):
        return values.max() - values.min()

    local_ranges = generic_filter(distance_map, local_range, footprint=kernel)
    voronoi_map = (local_ranges > 0).astype(np.uint8)
    voronoi_map[0, :] = 0
    voronoi_map[-1, :] = 0
    voronoi_map[:, 0] = 0
    voronoi_map[:, -1] = 0

    logging.debug("Created Voronoi map.")
    return voronoi_map

def skeletonize_voronoi(voronoi_map):
    """
    Skeletonizes the Voronoi map to obtain thin lines.

    Parameters:
        voronoi_map (numpy.ndarray): The Voronoi map.

    Returns:
        numpy.ndarray: The skeletonized Voronoi map image.
    """
    skeleton = skeletonize(voronoi_map )
    logging.debug("Skeletonized Voronoi map.")
    return skeleton

def save_as_png(image, filename):
    """
    Saves a NumPy array image as a PNG file.

    Parameters:
        image (numpy.ndarray): The image to save.
        filename (str): The path to the file to save the image.
    """
    if image.dtype != np.uint8:
        image_to_save = (image * 255).astype(np.uint8)
    else:
        image_to_save = image
    Image.fromarray(image_to_save).save(filename, format="PNG")
    logging.info(f"Image saved at {filename}")
