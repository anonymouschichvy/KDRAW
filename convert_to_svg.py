#!/usr/bin/env python3
import os
import sys
import time
import argparse
import collections
import numpy as np
from PIL import Image

def get_hex_color(val, has_alpha=False):
    """Convert packed pixel integer to SVG-compatible hex or rgba color string."""
    if has_alpha:
        a = (val >> 24) & 0xFF
        r = (val >> 16) & 0xFF
        g = (val >> 8) & 0xFF
        b = val & 0xFF
        if a < 255:
            return f"rgba({r},{g},{b},{a/255.0:.3f})"
        return f"#{r:02x}{g:02x}{b:02x}"
    else:
        r = (val >> 16) & 0xFF
        g = (val >> 8) & 0xFF
        b = val & 0xFF
        return f"#{r:02x}{g:02x}{b:02x}"

def smooth_paths_laplacian(path, iterations=3, weight=0.5):
    """
    Apply 1D Laplacian coordinate smoothing (moving average filter) to path.
    For closed loops, it wraps around to keep the loop closed.
    For open paths, endpoints are kept fixed.
    """
    if len(path) < 3:
        return path
        
    smoothed = np.copy(path)
    is_closed = np.linalg.norm(path[0] - path[-1]) < 1.0
    
    for _ in range(iterations):
        temp = np.copy(smoothed)
        if is_closed:
            n = len(temp) - 1
            for i in range(n):
                prev_idx = (i - 1) % n
                next_idx = (i + 1) % n
                smoothed[i] = (1 - weight) * temp[i] + weight * 0.5 * (temp[prev_idx] + temp[next_idx])
            smoothed[-1] = smoothed[0]
        else:
            for i in range(1, len(temp) - 1):
                smoothed[i] = (1 - weight) * temp[i] + weight * 0.5 * (temp[i-1] + temp[i+1])
    return smoothed

def smooth_paths_chaikin(path, iterations=2):
    """
    Apply Chaikin's corner-cutting subdivision algorithm to smooth a path.
    For closed loops, it wraps around to keep the loop closed.
    For open paths, endpoints are kept fixed.
    """
    if len(path) < 3:
        return path
        
    smoothed = np.copy(path)
    is_closed = np.linalg.norm(path[0] - path[-1]) < 1.0
    
    for _ in range(iterations):
        n = len(smoothed)
        new_pts = []
        if is_closed:
            for i in range(n - 1):
                p0 = smoothed[i]
                p1 = smoothed[i+1]
                q = 0.75 * p0 + 0.25 * p1
                r = 0.25 * p0 + 0.75 * p1
                new_pts.extend([q, r])
            new_pts.append(new_pts[0])
        else:
            new_pts.append(smoothed[0])
            for i in range(n - 1):
                p0 = smoothed[i]
                p1 = smoothed[i+1]
                q = 0.75 * p0 + 0.25 * p1
                r = 0.25 * p0 + 0.75 * p1
                new_pts.extend([q, r])
            new_pts.append(smoothed[-1])
        smoothed = np.array(new_pts, dtype=np.float32)
    return smoothed


def convert_pixel_perfect(input_path, output_path, num_colors=None):
    """
    Convert a raster image to SVG using an optimized Run-Length Box-Merging (RLBM) algorithm.
    This groups contiguous pixels of the same color into horizontal/vertical rectangular spans
    and combines them into a single path per color to minimize file size and render time.
    """
    print(f"Loading image {input_path}...")
    img = Image.open(input_path)
    width, height = img.size
    print(f"Dimensions: {width} x {height} ({width * height:,} pixels)")
    
    has_alpha = (img.mode == 'RGBA')
    if num_colors is not None and num_colors > 0:
        print(f"Quantizing image to {num_colors} colors...")
        start_q = time.time()
        if has_alpha:
            quantized = img.quantize(colors=num_colors, method=Image.Quantize.MEDIANCUT)
        else:
            quantized = img.convert('RGB').quantize(colors=num_colors, method=Image.Quantize.MEDIANCUT)
            
        palette = quantized.getpalette()
        data = np.array(quantized)
        
        def get_color_str(idx):
            r = palette[3 * idx]
            g = palette[3 * idx + 1]
            b = palette[3 * idx + 2]
            return f"#{r:02x}{g:02x}{b:02x}"
        print(f"Quantization finished in {time.time() - start_q:.3f} seconds.")
    else:
        print("Using lossless mode (no color quantization)...")
        if has_alpha:
            arr = np.array(img)
            data = (arr[:, :, 3].astype(np.uint32) << 24) | \
                   (arr[:, :, 0].astype(np.uint32) << 16) | \
                   (arr[:, :, 1].astype(np.uint32) << 8) | \
                   arr[:, :, 2].astype(np.uint32)
        else:
            arr = np.array(img.convert('RGB'))
            data = (arr[:, :, 0].astype(np.uint32) << 16) | \
                   (arr[:, :, 1].astype(np.uint32) << 8) | \
                   arr[:, :, 2].astype(np.uint32)
            
        def get_color_str(val):
            return get_hex_color(val, has_alpha)

    print("Running Run-Length Box-Merging algorithm...")
    start_merge = time.time()
    
    active_spans = {}
    rects_by_color = collections.defaultdict(list)
    
    for y in range(height):
        row = data[y]
        changes = np.where(row[:-1] != row[1:])[0]
        starts = np.concatenate(([0], changes + 1))
        ends = np.concatenate((changes + 1, [width]))
        colors = row[starts]
        
        next_active_spans = {}
        
        for start, end, color in zip(starts, ends, colors):
            key = (start, end, color)
            if key in active_spans:
                y_start = active_spans.pop(key)
                next_active_spans[key] = y_start
            else:
                next_active_spans[key] = y
                
        for key, y_start in active_spans.items():
            start, end, color = key
            color_str = get_color_str(color)
            rects_by_color[color_str].append((start, y_start, end - start, y - y_start))
            
        active_spans = next_active_spans

    for key, y_start in active_spans.items():
        start, end, color = key
        color_str = get_color_str(color)
        rects_by_color[color_str].append((start, y_start, end - start, height - y_start))
        
    print(f"Merging finished in {time.time() - start_merge:.3f} seconds.")
    
    total_rects = sum(len(rects) for rects in rects_by_color.values())
    unique_colors = len(rects_by_color)
    print(f"Found {total_rects:,} merged rectangles across {unique_colors:,} unique colors.")
    print(f"Average pixels per rectangle: {(width * height) / total_rects:.1f}")

    print(f"Writing SVG to {output_path}...")
    start_write = time.time()
    
    with open(output_path, 'w') as f:
        f.write(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" shape-rendering="crispEdges">\n')
        
        for color_str, rects in rects_by_color.items():
            path_d_parts = []
            for x, y, w, h in rects:
                path_d_parts.append(f"M{x},{y}h{w}v{h}h{-w}z")
            
            path_d = "".join(path_d_parts)
            f.write(f'  <path d="{path_d}" fill="{color_str}" />\n')
            
        f.write('</svg>\n')
        
    print(f"SVG written in {time.time() - start_write:.3f} seconds.")
    print(f"Output file size: {os.path.getsize(output_path) / 1024 / 1024:.2f} MB")

def convert_smooth(input_path, output_path, filter_speckle=4, color_precision=6, corner_threshold=60, path_precision=3):
    """
    Convert a raster image to SVG using the Rust-backed `vtracer` library.
    This creates smooth Bezier paths, perfect for vector logos or drawings.
    """
    try:
        import vtracer
    except ImportError:
        print("\n[ERROR] The 'vtracer' package is not installed.")
        print("To use smooth vectorization, please install it via:")
        print("    pip install vtracer")
        return False
        
    print(f"Vectorizing {input_path} with vtracer (smooth curves)...")
    start = time.time()
    
    vtracer.convert_image_to_svg_py(
        input_path,
        output_path,
        colormode='color',
        hierarchical='stacked',
        mode='spline',
        filter_speckle=filter_speckle,
        color_precision=color_precision,
        corner_threshold=corner_threshold,
        path_precision=path_precision
    )
    
    print(f"Smooth vectorization completed in {time.time() - start:.3f} seconds.")
    print(f"Output file size: {os.path.getsize(output_path) / 1024:.2f} KB")
    return True

def optimize_paths(contours, max_join_dist=0.0):
    """
    Sort and orient contours to minimize pen-up travel distance (greedy TSP solver).
    Optionally merges close endpoints within max_join_dist to eliminate pen lifts.
    Returns list of optimized contours, unoptimized travel, and optimized travel distance.
    """
    if not contours or len(contours) == 0:
        return [], 0.0, 0.0
        
    formatted = [c.reshape(-1, 2) for c in contours if len(c) > 0]
    if not formatted:
        return [], 0.0, 0.0
        
    unopt_travel = 0.0
    for i in range(len(formatted) - 1):
        unopt_travel += np.linalg.norm(formatted[i][-1] - formatted[i+1][0])
        
    optimized = []
    remaining = list(formatted)
    
    current_path = list(remaining.pop(0))
    current_pos = current_path[-1]
    
    opt_travel = 0.0
    
    while remaining:
        starts = np.array([c[0] for c in remaining])
        ends = np.array([c[-1] for c in remaining])
        
        dist_starts = np.sum((starts - current_pos) ** 2, axis=1)
        dist_ends = np.sum((ends - current_pos) ** 2, axis=1)
        
        min_start_idx = np.argmin(dist_starts)
        min_end_idx = np.argmin(dist_ends)
        
        d_start = dist_starts[min_start_idx]
        d_end = dist_ends[min_end_idx]
        
        if d_start <= d_end:
            best_idx = min_start_idx
            best_dist = np.sqrt(d_start)
            reverse_path = False
        else:
            best_idx = min_end_idx
            best_dist = np.sqrt(d_end)
            reverse_path = True
            
        opt_travel += best_dist
        next_path = remaining.pop(best_idx)
        if reverse_path:
            next_path = next_path[::-1]
            
        if max_join_dist > 0.0 and best_dist <= max_join_dist:
            # Merge paths to prevent pen lift
            current_path.extend(next_path)
        else:
            # Save the completed path and start a new one
            optimized.append(np.array(current_path, dtype=np.float32))
            current_path = list(next_path)
            
        current_pos = current_path[-1]
        
    if current_path:
        optimized.append(np.array(current_path, dtype=np.float32))
        
    return optimized, unopt_travel, opt_travel

def convert_plotter(input_path, output_path, use_canny=False, threshold_val=None, epsilon=0.5, no_sort=False, blur_size=0, max_join=2.0,
                    smooth_type='chaikin', smooth_iters=0, smooth_weight=0.5, smooth_decimate=0.0):
    """
    Extract contour paths from image, simplify them, sort them to minimize pen travel,
    and output a stroke-only SVG ideal for a CNC pen plotter.
    """
    try:
        import cv2
    except ImportError:
        print("\n[ERROR] The 'opencv-python' package is required for CNC Plotter Mode.")
        print("Please install it via:")
        print("    pip install opencv-python")
        return False
        
    print(f"Loading image {input_path} in grayscale...")
    img = cv2.imread(input_path)
    if img is None:
        print(f"Error: Could not load image {input_path} with OpenCV.")
        sys.exit(1)
        
    height, width = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    
    if blur_size > 0:
        if blur_size % 2 == 0:
            blur_size += 1
        print(f"Applying Gaussian Blur (kernel={blur_size}x{blur_size}) to smooth outlines...")
        gray = cv2.GaussianBlur(gray, (blur_size, blur_size), 0)
    
    if use_canny:
        print("Using Canny Edge Detection...")
        median_val = np.median(gray)
        lower = int(max(0, 0.66 * median_val))
        upper = int(min(255, 1.33 * median_val))
        print(f"Canny auto-thresholds: lower={lower}, upper={upper}")
        processed = cv2.Canny(gray, lower, upper)
    else:
        print("Using Thresholding...")
        if threshold_val is None:
            print("Applying Otsu's adaptive thresholding...")
            _, processed = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        else:
            print(f"Applying binary thresholding at value {threshold_val}...")
            _, processed = cv2.threshold(gray, threshold_val, 255, cv2.THRESH_BINARY_INV)
            
    contours, _ = cv2.findContours(processed, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    print(f"Extracted {len(contours):,} raw contours.")
    
    simplified = []
    total_raw_points = sum(len(c) for c in contours)
    for c in contours:
        if len(c) < 2:
            continue
        approx = cv2.approxPolyDP(c, epsilon, True)
        if len(approx) > 1:
            simplified.append(approx.reshape(-1, 2))
            
    # Apply Path Smoothing and Optional Decimation
    if smooth_iters > 0:
        print(f"Applying {smooth_type} path smoothing ({smooth_iters} iterations)...")
        smoothed_paths = []
        for p in simplified:
            if smooth_type.lower() == 'chaikin':
                sp = smooth_paths_chaikin(p, smooth_iters)
            else:
                sp = smooth_paths_laplacian(p, smooth_iters, smooth_weight)
                
            if smooth_decimate > 0.0 and len(sp) >= 2:
                sp_reshaped = sp.reshape(-1, 1, 2)
                approx = cv2.approxPolyDP(sp_reshaped, smooth_decimate, True)
                sp = approx.reshape(-1, 2)
                
            if len(sp) >= 2:
                smoothed_paths.append(sp)
        simplified = smoothed_paths
            
    total_simp_points = sum(len(c) for c in simplified)
    print(f"Simplified to {len(simplified):,} contours.")
    print(f"Reduced points from {total_raw_points:,} to {total_simp_points:,} ({(1 - total_simp_points/max(1, total_raw_points))*100:.1f}% reduction).")
    
    if not no_sort:
        print("Optimizing path sequences to minimize pen travel (TSP)...")
        start_sort = time.time()
        optimized, unopt_travel, opt_travel = optimize_paths(simplified, max_join)
        sort_time = time.time() - start_sort
        print(f"TSP optimization finished in {sort_time:.3f} seconds.")
        if unopt_travel > 0:
            saved = (1 - opt_travel / unopt_travel) * 100
            print(f"Pen-up travel distance reduced from {unopt_travel:.1f}px to {opt_travel:.1f}px ({saved:.1f}% travel saved!).")
    else:
        print("Skipping path sequence optimization.")
        optimized = [c.reshape(-1, 2) for c in simplified]
        
    print(f"Writing stroke-only SVG to {output_path}...")
    start_write = time.time()
    
    with open(output_path, 'w') as f:
        f.write(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">\n')
        
        path_d_parts = []
        for contour in optimized:
            if len(contour) < 2:
                continue
            d_str = f"M{contour[0][0]:.2f},{contour[0][1]:.2f}"
            for pt in contour[1:]:
                d_str += f"L{pt[0]:.2f},{pt[1]:.2f}"
            d_str += "z"
            path_d_parts.append(d_str)
            
        path_d = " ".join(path_d_parts)
        f.write(f'  <path d="{path_d}" fill="none" stroke="black" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />\n')
        f.write('</svg>\n')
        
    print(f"SVG written in {time.time() - start_write:.3f} seconds.")
    print(f"Output file size: {os.path.getsize(output_path) / 1024:.2f} KB")
    return True

def build_and_prune_graph(skel_bool, min_spur_length=16, collapse_dist=8):
    pixels = set(zip(*np.where(skel_bool)))
    
    # Compute adjacency
    adj = {}
    for p in pixels:
        y, x = p
        candidates = [
            (y-1, x-1), (y-1, x), (y-1, x+1),
            (y, x-1),             (y, x+1),
            (y+1, x-1), (y+1, x), (y+1, x+1)
        ]
        adj[p] = [c for c in candidates if c in pixels]
        
    # Classify pixels
    endpoints = {p for p, neighbors in adj.items() if len(neighbors) == 1}
    junctions = {p for p, neighbors in adj.items() if len(neighbors) >= 3}
    regular = {p for p, neighbors in adj.items() if len(neighbors) == 2}
    
    # Group junction pixels into clusters (each cluster is a node)
    visited_junc = set()
    junc_clusters = []
    for j in junctions:
        if j in visited_junc:
            continue
        cluster = []
        queue = [j]
        visited_junc.add(j)
        while queue:
            curr = queue.pop(0)
            cluster.append(curr)
            for n in adj[curr]:
                if n in junctions and n not in visited_junc:
                    visited_junc.add(n)
                    queue.append(n)
        junc_clusters.append(cluster)
        
    # Create node mapping: pixel -> node_id
    node_to_pixels = {}
    pixel_to_node = {}
    node_id_counter = 0
    
    for ep in endpoints:
        node_to_pixels[node_id_counter] = [ep]
        pixel_to_node[ep] = node_id_counter
        node_id_counter += 1
        
    for jc in junc_clusters:
        node_to_pixels[node_id_counter] = jc
        for j in jc:
            pixel_to_node[j] = node_id_counter
        node_id_counter += 1
        
    # Trace edges connecting nodes
    edges = []
    edge_id_counter = 0
    visited_regular = set()
    added_direct = set()
    
    def get_node_of_pixel(px):
        return pixel_to_node.get(px, None)
        
    for node_id, node_pxs in node_to_pixels.items():
        for start_px in node_pxs:
            for neighbor in adj[start_px]:
                if neighbor in regular and neighbor not in visited_regular:
                    # Start tracing an edge through regular pixels
                    path = [start_px, neighbor]
                    visited_regular.add(neighbor)
                    curr = neighbor
                    
                    while True:
                        next_candidates = [n for n in adj[curr] if n != path[-2]]
                        if not next_candidates:
                            break
                        next_px = None
                        for n in next_candidates:
                            if n in regular:
                                if n not in visited_regular:
                                    next_px = n
                                    break
                            elif n in pixel_to_node:
                                next_px = n
                                break
                        if next_px is None:
                            break
                            
                        path.append(next_px)
                        if next_px in regular:
                            visited_regular.add(next_px)
                            curr = next_px
                        else:
                            break
                            
                    end_px = path[-1]
                    end_node_id = get_node_of_pixel(end_px)
                    if end_node_id is not None:
                        edges.append({
                            'id': edge_id_counter,
                            'p1': node_id,
                            'p2': end_node_id,
                            'path': path
                        })
                        edge_id_counter += 1
                elif neighbor in pixel_to_node:
                    # Direct node-to-node connection
                    neighbor_node_id = pixel_to_node[neighbor]
                    if node_id != neighbor_node_id:
                        pair = tuple(sorted((node_id, neighbor_node_id)))
                        if pair not in added_direct:
                            added_direct.add(pair)
                            edges.append({
                                'id': edge_id_counter,
                                'p1': node_id,
                                'p2': neighbor_node_id,
                                'path': [start_px, neighbor]
                            })
                            edge_id_counter += 1
                            
    # Find isolated loops
    for p in regular:
        if p not in visited_regular:
            path = [p]
            visited_regular.add(p)
            curr = p
            while True:
                next_candidates = [n for n in adj[curr] if n in regular and n not in visited_regular]
                if not next_candidates:
                    break
                next_px = next_candidates[0]
                path.append(next_px)
                visited_regular.add(next_px)
                curr = next_px
            if len(path) > 2:
                if path[0] in adj[path[-1]]:
                    path.append(path[0])
                    dummy_node = node_id_counter
                    node_to_pixels[dummy_node] = [path[0]]
                    node_id_counter += 1
                    edges.append({
                        'id': edge_id_counter,
                        'p1': dummy_node,
                        'p2': dummy_node,
                        'path': path
                    })
                    edge_id_counter += 1
                    
    # Prune spurs and collapse short edges
    changed = True
    while changed:
        changed = False
        node_degrees = collections.defaultdict(int)
        for e in edges:
            node_degrees[e['p1']] += 1
            node_degrees[e['p2']] += 1
            
        spur_to_remove = None
        for e in edges:
            u, v = e['p1'], e['p2']
            if u == v:
                continue
            deg_u = node_degrees[u]
            deg_v = node_degrees[v]
            length = len(e['path'])
            
            is_spur = False
            if (deg_u == 1 and deg_v >= 3) or (deg_v == 1 and deg_u >= 3):
                is_spur = (length < min_spur_length)
            elif deg_u == 1 and deg_v == 1:
                # Isolated path (i-dots, punctuation). Keep all of them.
                is_spur = False
                
            if is_spur:
                spur_to_remove = e
                break
                
        if spur_to_remove:
            edges.remove(spur_to_remove)
            changed = True
            continue
            
        edge_to_collapse = None
        for e in edges:
            u, v = e['p1'], e['p2']
            if u == v:
                continue
            deg_u = node_degrees[u]
            deg_v = node_degrees[v]
            length = len(e['path'])
            
            if deg_u >= 3 and deg_v >= 3 and length <= collapse_dist:
                edge_to_collapse = e
                break
                
        if edge_to_collapse:
            u = edge_to_collapse['p1']
            v = edge_to_collapse['p2']
            edges.remove(edge_to_collapse)
            for e in edges:
                if e['p1'] == v: e['p1'] = u
                if e['p2'] == v: e['p2'] = u
            node_to_pixels[u].extend(node_to_pixels[v])
            del node_to_pixels[v]
            changed = True
            continue
            
    # Merge degree 2 nodes
    node_degrees = collections.defaultdict(int)
    node_edges = collections.defaultdict(list)
    for e in edges:
        node_edges[e['p1']].append(e)
        node_edges[e['p2']].append(e)
        node_degrees[e['p1']] += 1
        node_degrees[e['p2']] += 1
        
    degree_2_nodes = [node_id for node_id, deg in node_degrees.items() if deg == 2]
    for node_id in degree_2_nodes:
        node_es = node_edges[node_id]
        if len(node_es) == 2:
            e1, e2 = node_es[0], node_es[1]
            if e1['id'] != e2['id']:
                p1_pts = list(e1['path'])
                p2_pts = list(e2['path'])
                shared_pixels = set(node_to_pixels[node_id])
                
                p1_start_in_shared = p1_pts[0] in shared_pixels
                p1_end_in_shared = p1_pts[-1] in shared_pixels
                p2_start_in_shared = p2_pts[0] in shared_pixels
                p2_end_in_shared = p2_pts[-1] in shared_pixels
                
                if p1_end_in_shared and p2_start_in_shared:
                    merged_path = p1_pts[:-1] + p2_pts
                    new_p1 = e1['p1'] if e1['p2'] == node_id else e1['p2']
                    new_p2 = e2['p2'] if e2['p1'] == node_id else e2['p1']
                elif p1_end_in_shared and p2_end_in_shared:
                    merged_path = p1_pts[:-1] + p2_pts[::-1]
                    new_p1 = e1['p1'] if e1['p2'] == node_id else e1['p2']
                    new_p2 = e2['p1'] if e2['p2'] == node_id else e2['p2']
                elif p1_start_in_shared and p2_start_in_shared:
                    merged_path = p1_pts[::-1][:-1] + p2_pts
                    new_p1 = e1['p2'] if e1['p1'] == node_id else e1['p1']
                    new_p2 = e2['p2'] if e2['p1'] == node_id else e2['p1']
                else:
                    merged_path = p2_pts[:-1] + p1_pts
                    new_p1 = e2['p1'] if e2['p2'] == node_id else e2['p2']
                    new_p2 = e1['p2'] if e1['p1'] == node_id else e1['p1']
                    
                edges.remove(e1)
                edges.remove(e2)
                new_edge = {
                    'id': e1['id'],
                    'p1': new_p1,
                    'p2': new_p2,
                    'path': merged_path
                }
                edges.append(new_edge)
                node_edges[new_p1] = [e for e in node_edges[new_p1] if e['id'] not in (e1['id'], e2['id'])] + [new_edge]
                node_edges[new_p2] = [e for e in node_edges[new_p2] if e['id'] not in (e1['id'], e2['id'])] + [new_edge]
                
    return [np.array([(pt[1], pt[0]) for pt in e['path']], dtype=np.float32) for e in edges]

def convert_centerline(input_path, output_path, threshold_val=None, epsilon=0.3, no_sort=False,
                       invert_threshold=False, blur_size=9, use_adaptive=False, block_size=15, c_val=10,
                       min_spur_length=16, max_join=2.5, loop_gap=0.0, min_path_len=0.0,
                       smooth_type='chaikin', smooth_iters=3, smooth_weight=0.5, smooth_decimate=0.1,
                       upscale_factor=4, morph_close=5, morph_open=0, collapse_junc=8):
    """
    Skeletonize the image to a 1-pixel-wide centerline, trace it into single-line paths,
    prune short spurs using graph topology, and output a stroke-only SVG with minimized pen-up travel.
    """
    try:
        import cv2
    except ImportError:
        print("\n[ERROR] The 'opencv-python' package is required for Centerline Mode.")
        print("Please install it via:")
        print("    pip install opencv-python")
        return False
        
    try:
        from skimage.morphology import skeletonize
    except ImportError:
        print("\n[ERROR] The 'scikit-image' package is required for Centerline Mode.")
        print("Please install it via:")
        print("    pip install scikit-image")
        return False
        
    print(f"Loading image {input_path} in grayscale...")
    img = cv2.imread(input_path)
    if img is None:
        print(f"Error: Could not load image {input_path} with OpenCV.")
        sys.exit(1)
        
    height, width = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    
    # 1. Upscale if requested
    if upscale_factor > 1:
        print(f"Upscaling input image by {upscale_factor}x for smooth curve definition...")
        width_up, height_up = width * upscale_factor, height * upscale_factor
        gray = cv2.resize(gray, (width_up, height_up), interpolation=cv2.INTER_CUBIC)
        if blur_size > 0:
            blur_size = int(blur_size)
            if blur_size % 2 == 0:
                blur_size += 1
        
    # 2. Apply Gaussian Blur to smooth pixelated edges and JPEG compression wiggles
    if blur_size > 0:
        if blur_size % 2 == 0:
            blur_size += 1
        print(f"Applying Gaussian Blur (kernel={blur_size}x{blur_size}) to smooth wiggles...")
        gray = cv2.GaussianBlur(gray, (blur_size, blur_size), 0)
        
    # 3. Apply Thresholding
    if use_adaptive:
        print(f"Applying Adaptive Gaussian Thresholding (blockSize={block_size}, C={c_val})...")
        if block_size % 2 == 0:
            block_size += 1
        block_size = max(3, block_size)
        
        thresh_type = cv2.THRESH_BINARY if invert_threshold else cv2.THRESH_BINARY_INV
        processed = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, thresh_type, block_size, c_val
        )
    else:
        thresh_type = cv2.THRESH_BINARY if invert_threshold else cv2.THRESH_BINARY_INV
        if threshold_val is None:
            print("Applying Otsu's adaptive thresholding...")
            _, processed = cv2.threshold(gray, 0, 255, thresh_type + cv2.THRESH_OTSU)
        else:
            print(f"Applying binary thresholding at value {threshold_val}...")
            _, processed = cv2.threshold(gray, threshold_val, 255, thresh_type)
            
    # 4. Apply Morphological closing/opening if upscaled
    if upscale_factor > 1:
        if morph_close > 0:
            print(f"Applying morphological closing (kernel={morph_close}x{morph_close})...")
            kernel_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (morph_close, morph_close))
            processed = cv2.morphologyEx(processed, cv2.MORPH_CLOSE, kernel_close)
        if morph_open > 0:
            print(f"Applying morphological opening (kernel={morph_open}x{morph_open})...")
            kernel_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (morph_open, morph_open))
            processed = cv2.morphologyEx(processed, cv2.MORPH_OPEN, kernel_open)
            
    # 5. Skeletonize (thinning to 1-pixel centerline)
    print("Skeletonizing image...")
    start_skel = time.time()
    binary_bool = (processed > 0)
    skel_bool = skeletonize(binary_bool)
    print(f"Skeletonization completed in {time.time() - start_skel:.3f} seconds.")
    
    # 6. Graph-based tracing and pruning
    print(f"Tracing centerline paths with graph topology (min_spur={min_spur_length}, collapse_junc={collapse_junc})...")
    start_trace = time.time()
    paths = build_and_prune_graph(skel_bool, min_spur_length=min_spur_length, collapse_dist=collapse_junc)
    print(f"Tracing finished in {time.time() - start_trace:.3f} seconds. Extracted {len(paths):,} paths.")
    
    # 7. Scale coordinates back and simplify paths
    processed_paths = []
    pruned_count = 0
    total_raw_points = sum(len(p) for p in paths)
    
    for p in paths:
        if len(p) < 2:
            continue
            
        # Scale back to original coordinates
        if upscale_factor > 1:
            p_scaled = p / upscale_factor
        else:
            p_scaled = p
            
        # Pruning short paths
        if min_path_len > 0.0:
            p_len = float(np.sum(np.sqrt(np.sum(np.diff(p_scaled, axis=0)**2, axis=1))))
            if p_len < min_path_len:
                pruned_count += 1
                continue
                
        # Simplify path using RDP
        if len(p_scaled) == 2:
            approx = p_scaled
        else:
            p_reshaped = p_scaled.reshape(-1, 1, 2)
            approx = cv2.approxPolyDP(p_reshaped, epsilon, False).reshape(-1, 2)
            
        if len(approx) >= 2:
            processed_paths.append(approx)
            
    paths = processed_paths
    if min_path_len > 0.0:
        print(f"Pruned {pruned_count:,} short paths (length < {min_path_len}px).")
        
    # 8. Path Smoothing and Post-decimation
    if smooth_iters > 0:
        print(f"Applying {smooth_type} path smoothing ({smooth_iters} iterations)...")
        smoothed_paths = []
        for p in paths:
            if smooth_type.lower() == 'chaikin':
                sp = smooth_paths_chaikin(p, smooth_iters)
            else:
                sp = smooth_paths_laplacian(p, smooth_iters, smooth_weight)
                
            if smooth_decimate > 0.0 and len(sp) > 2:
                sp_reshaped = sp.reshape(-1, 1, 2)
                approx = cv2.approxPolyDP(sp_reshaped, smooth_decimate, False)
                sp = approx.reshape(-1, 2)
                
            if len(sp) >= 2:
                smoothed_paths.append(sp)
        paths = smoothed_paths
        
    total_simp_points = sum(len(p) for p in paths)
    print(f"Simplified to {len(paths):,} paths.")
    print(f"Reduced points from {total_raw_points:,} to {total_simp_points:,} ({(1 - total_simp_points/max(1, total_raw_points))*100:.1f}% reduction).")
    
    # 9. Optimize path sequence (TSP)
    if not no_sort:
        print("Optimizing path sequences to minimize pen travel (TSP)...")
        start_sort = time.time()
        optimized, unopt_travel, opt_travel = optimize_paths(paths, max_join)
        sort_time = time.time() - start_sort
        print(f"TSP optimization finished in {sort_time:.3f} seconds.")
        if unopt_travel > 0:
            saved = (1 - opt_travel / unopt_travel) * 100
            print(f"Pen-up travel distance reduced from {unopt_travel:.1f}px to {opt_travel:.1f}px ({saved:.1f}% travel saved!).")
    else:
        print("Skipping path sequence optimization.")
        optimized = paths
        
    print(f"Writing centerline SVG to {output_path}...")
    start_write = time.time()
    
    with open(output_path, 'w') as f:
        f.write(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}">\n')
        
        path_d_parts = []
        for path in optimized:
            if len(path) < 2:
                continue
            d_str = f"M{path[0][0]:.2f},{path[0][1]:.2f}"
            for pt in path[1:]:
                d_str += f"L{pt[0]:.2f},{pt[1]:.2f}"
            path_d_parts.append(d_str)
            
        path_d = " ".join(path_d_parts)
        f.write(f'  <path d="{path_d}" fill="none" stroke="black" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />\n')
        f.write('</svg>\n')
        
    print(f"SVG written in {time.time() - start_write:.3f} seconds.")
    print(f"Output file size: {os.path.getsize(output_path) / 1024 / 1024:.2f} MB" if os.path.getsize(output_path) > 1024*1024 else f"Output file size: {os.path.getsize(output_path) / 1024:.2f} KB")
    return True

def main():
    parser = argparse.ArgumentParser(description="Convert an image (JPG/PNG) into an optimized SVG path document.")
    parser.add_argument("input_path", help="Path to the input image (e.g. a.jpg)")
    parser.add_argument("output_path", nargs="?", help="Path to the output SVG (defaults to <input_name>.svg)")
    parser.add_argument("-c", "--colors", type=int, default=None,
                        help="Reduce image to N colors using quantization before merging (highly recommended for JPEGs)")
    parser.add_argument("-l", "--lossless", action="store_true",
                        help="Convert exact pixel colors without quantization (can result in very large files for JPEGs)")
    parser.add_argument("-s", "--smooth", action="store_true",
                        help="Generate smooth vectorized curves instead of pixel-perfect blocks (requires vtracer)")
    
    # CNC Plotter arguments
    parser.add_argument("-p", "--plotter", action="store_true",
                        help="Generate stroke-only SVG paths optimized for a CNC pen plotter")
    parser.add_argument("-cl", "--centerline", action="store_true",
                        help="Generate single-line centerpaths using skeletonization to eliminate bubble letters")
    parser.add_argument("--invert", action="store_true",
                        help="Invert thresholding in centerline mode (for white lines on a dark background)")
    parser.add_argument("--canny", action="store_true",
                        help="Use Canny edge detection instead of thresholding for plotter mode")
    parser.add_argument("--threshold", type=int, default=None,
                        help="Custom binary threshold value (0-255) for plotter/centerline mode")
    parser.add_argument("--epsilon", type=float, default=0.3,
                        help="Path simplification distance tolerance in pixels (default: 0.3)")
    parser.add_argument("--no-sort", action="store_true",
                        help="Skip TSP path sorting/sequence optimization")
    parser.add_argument("--max-join", type=float, default=2.5,
                        help="Join path endpoints within this distance in pixels to reduce pen lifts (default: 2.5)")
    
    # Path smoothing options
    parser.add_argument("--smooth-type", choices=["laplacian", "chaikin"], default="chaikin",
                        help="Smoothing algorithm to use: laplacian or chaikin (default: chaikin)")
    parser.add_argument("--smooth-iters", type=int, default=3,
                        help="Number of smoothing passes/iterations (default: 3)")
    parser.add_argument("--smooth-weight", type=float, default=0.5,
                        help="Laplacian smoothing blend factor between 0.0 and 1.0 (default: 0.5)")
    parser.add_argument("--smooth-decimate", type=float, default=0.1,
                        help="Post-smoothing RDP decimation epsilon tolerance (default: 0.1)")
    
    # Image processing enhancements
    parser.add_argument("--blur", type=int, default=9,
                        help="Gaussian blur kernel size to smooth out pixelation wiggles (default: 9)")
    parser.add_argument("--no-adaptive", action="store_true",
                        help="Disable Adaptive Gaussian thresholding and use global thresholding instead")
    parser.add_argument("--block-size", type=int, default=15,
                        help="Local neighborhood block size for adaptive thresholding (default: 15)")
    parser.add_argument("--c-val", type=int, default=10,
                        help="Constant subtracted from local mean for adaptive thresholding; higher makes lines thinner (default: 10)")
    parser.add_argument("--min-spur", type=int, default=16,
                        help="Minimum pixel length for a skeleton branch to not be pruned as a spur (default: 16)")
    parser.add_argument("--loop-gap", type=float, default=0.0,
                        help="Width of gap in pixels to open small closed loops (e.g. 5.0 to 8.0) (default: 0.0)")
    parser.add_argument("--min-path-len", type=float, default=0.0,
                        help="Minimum length of a path in pixels to keep; shorter paths are pruned (default: 0.0)")
    
    # Upscaling & Advanced Graph-based Truning arguments
    parser.add_argument("--upscale", type=int, default=4,
                        help="Upscale factor to smooth out pixelation wiggles during centerline mode (default: 4)")
    parser.add_argument("--morph-close", type=int, default=5,
                        help="Morphological closing kernel size on upscaled image to fill gaps (default: 5)")
    parser.add_argument("--morph-open", type=int, default=0,
                        help="Morphological opening kernel size on upscaled image to smooth contours (default: 0)")
    parser.add_argument("--collapse-junc", type=int, default=8,
                        help="Distance in pixels below which adjacent junctions will be collapsed (default: 8)")
    
    # Advanced vtracer parameters
    parser.add_argument("--filter-speckle", type=int, default=4, help="Speckle filter size for smooth vectorization")
    parser.add_argument("--color-precision", type=int, default=6, help="Color precision (significant bits) for smooth vectorization")
    parser.add_argument("--corner-threshold", type=int, default=60, help="Corner threshold angle for smooth vectorization")
    parser.add_argument("--path-precision", type=int, default=3, help="Decimal precision of path coordinates")

    args = parser.parse_args()
    
    if not os.path.exists(args.input_path):
        print(f"Error: Input file '{args.input_path}' not found.")
        sys.exit(1)
        
    output_path = args.output_path
    if not output_path:
        base, _ = os.path.splitext(args.input_path)
        if args.centerline:
            suffix = "_centerline"
        elif args.plotter:
            suffix = "_plotter"
        else:
            suffix = ""
        output_path = base + suffix + ".svg"
        
    if args.centerline:
        success = convert_centerline(
            args.input_path, output_path,
            threshold_val=args.threshold,
            epsilon=args.epsilon,
            no_sort=args.no_sort,
            invert_threshold=args.invert,
            blur_size=args.blur,
            use_adaptive=not args.no_adaptive,
            block_size=args.block_size,
            c_val=args.c_val,
            min_spur_length=args.min_spur,
            max_join=args.max_join,
            loop_gap=args.loop_gap,
            min_path_len=args.min_path_len,
            smooth_type=args.smooth_type,
            smooth_iters=args.smooth_iters,
            smooth_weight=args.smooth_weight,
            smooth_decimate=args.smooth_decimate,
            upscale_factor=args.upscale,
            morph_close=args.morph_close,
            morph_open=args.morph_open,
            collapse_junc=args.collapse_junc
        )
        if not success:
            sys.exit(1)
    elif args.plotter:
        success = convert_plotter(
            args.input_path, output_path,
            use_canny=args.canny,
            threshold_val=args.threshold,
            epsilon=args.epsilon,
            no_sort=args.no_sort,
            blur_size=args.blur,
            max_join=args.max_join,
            smooth_type=args.smooth_type,
            smooth_iters=args.smooth_iters,
            smooth_weight=args.smooth_weight,
            smooth_decimate=args.smooth_decimate
        )
        if not success:
            sys.exit(1)
    elif args.smooth:
        success = convert_smooth(
            args.input_path, output_path,
            filter_speckle=args.filter_speckle,
            color_precision=args.color_precision,
            corner_threshold=args.corner_threshold,
            path_precision=args.path_precision
        )
        if not success:
            sys.exit(1)
    else:
        num_colors = args.colors
        if not args.lossless and num_colors is None:
            _, ext = os.path.splitext(args.input_path.lower())
            if ext in ('.jpg', '.jpeg'):
                print("WARNING: Input is a JPEG and no quantization is specified.")
                print("JPEG compression noise will prevent efficient pixel merging, producing a huge SVG.")
                print("Defaulting to 64 colors quantization for efficiency. Use --lossless to override.")
                num_colors = 64
        
        convert_pixel_perfect(args.input_path, output_path, num_colors=num_colors)

if __name__ == "__main__":
    main()
