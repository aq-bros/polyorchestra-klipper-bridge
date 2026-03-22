#  Polyorchestra™ Klipper Bridge
#  Version 0.1.0 (Beta)
#  Copyright (c) 2025-2026 AQ Bros. All rights reserved.
#
#  "Polyorchestra" is a registered trademark of AQ Bros.
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU Affero General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.

import websocket
import json
import time
import os
import requests
import uuid
import random
import threading
import base64
import re
from datetime import datetime
from typing import Optional

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(CURRENT_DIR, "config.json")

config = {}
ws_app: Optional[websocket.WebSocketApp] = None
pending_code = None
is_paired = False
force_full_sync = False
BRIDGE_TYPE = "klipper"
sync_lock = threading.Lock()
PRODUCTION_MODE = True
calibrating_profile_id = None
is_suspended_billing = False
sync_interval = 5.0
global_history_map = {}

cache = {
    "status": "", "progress": -1,
    "status_message": "",
    "raw_print_state": "", "raw_idle_state": "", "raw_webhooks_state": "ready",
    "temp_bed": 0, "target_bed": 0,
    "temp_nozzle": 0, "target_nozzle": 0,
    "temp_chamber": 0, "temp_pi": 0, "temp_mcu": 0,
    "fan_speed": 0, "speed_factor": 100, "flow_factor": 100,
    "z_offset": 0.0, "pos_x": 0.0, "pos_y": 0.0, "pos_z": 0.0,
    "time_total": 0, "time_remaining": 0, "print_duration": 0,
    "filament_used": 0.0, "print_speed": 0,
    "pressure_advance": 0.0, "smooth_time": 0.04,
    "velocity_scv": 5.0, "accel": 0,
    "minimum_cruise_ratio": 0.5,
    "light_intensity": 0, "filename": "",
    "raw_m73_progress": 0,
    "raw_file_progress": 0
}

last_sent_cache = cache.copy()
last_heartbeat = 0
last_user_active_ts = 0
last_z_update_ts = 0
last_history_ts = 0
last_print_update_ts = 0
last_api_call_ts = 0
last_file_action_ts = 0
current_file_metadata_time = 0
current_file_metadata_filament = 0

def log(msg):
    if not PRODUCTION_MODE:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

def detect_moonraker_port():
    home = os.path.expanduser("~")
    paths = [
        os.path.join(home, "printer_data", "config", "moonraker.conf"),
        os.path.join(home, "klipper_config", "moonraker.conf")
    ]
    for p in paths:
        if os.path.exists(p):
            try:
                with open(p, "r") as f:
                    for line in f:
                        match = re.match(r"^\s*port:\s*(\d+)", line)
                        if match:
                            return int(match.group(1))
            except:
                pass
    return 7125

def load_config():
    global config
    try:
        with open(CONFIG_PATH, "r") as f:
            config = json.load(f)
    except:
        detected_port = detect_moonraker_port()
        config = {
            "supabase_url": "https://phciwqkkvtqbiuijhctg.supabase.co",
            "supabase_key": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InBoY2l3cWtrdnRxYml1aWpoY3RnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjU1NTIxNzAsImV4cCI6MjA4MTEyODE3MH0.uMhTH6mp0o7F4eUCFwFHPxfgkFMsZSJAiTNTLAZZL_w",
            "moonraker_host": "127.0.0.1",
            "moonraker_port": detected_port
        }
        save_config()

def save_config():
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=4)
        f.flush()
        os.fsync(f.fileno())

def get_headers(json_content=True):
    h = {
        "apikey": config.get('supabase_key', ''),
        "Authorization": f"Bearer {config.get('supabase_key', '')}",
        "x-polyorchestra-token": config.get('api_secret', '')
    }
    if json_content:
        h["Content-Type"] = "application/json"
    return h

def safe_int(val):
    try:
        if val is None: return 0
        return int(float(val))
    except:
        return 0

def send_gcode(gcode):
    if ws_app and ws_app.sock and ws_app.sock.connected:
        ws_app.send(json.dumps({"jsonrpc": "2.0", "method": "printer.gcode.script", "params": {"script": gcode},
                                "id": random.randint(1, 10000)}))

def ensure_registration():
    global config, pending_code
    load_config()
    if not config.get("supabase_url") or not config.get("supabase_key"):
        return False
    if config.get("device_id"): return True
    new_id = f"bridge-mac-{uuid.uuid4().hex[:8]}"
    new_code = str(random.randint(100000, 999999))
    url = f"{config['supabase_url']}/rest/v1/rpc/register_new_printer"
    payload = {"p_device_id": new_id, "p_claim_code": new_code, "p_bridge_type": BRIDGE_TYPE}
    try:
        r = requests.post(url, json=payload, headers=get_headers(), timeout=5)
        if r.status_code in [200, 204]:
            config['device_id'] = new_id
            save_config()
            pending_code = new_code
            return True
        return False
    except:
        return False

def reset_cache_values():
    global cache, current_file_metadata_time, current_file_metadata_filament
    cache["temp_bed"] = -999
    cache["temp_nozzle"] = -999
    cache["status"] = "force_update"
    cache["progress"] = -999
    cache["raw_m73_progress"] = 0
    cache["raw_file_progress"] = 0
    current_file_metadata_time = 0
    current_file_metadata_filament = 0

def fetch_current_file_metadata(filename):
    global current_file_metadata_time, current_file_metadata_filament
    if not filename: return
    try:
        host = config.get("moonraker_host", "127.0.0.1")
        port = config.get("moonraker_port", 7125)
        url = f"http://{host}:{port}/server/files/metadata?filename={filename}"
        for _ in range(5):
            r = requests.get(url, timeout=3)
            if r.status_code == 200:
                data = r.json().get("result", {})
                est_time = data.get("estimated_time", 0)
                if est_time > 0:
                    current_file_metadata_time = int(est_time)
                    current_file_metadata_filament = float(data.get("filament_total", 0))
                    break
            time.sleep(1)
    except:
        pass

def update_file_status_in_db(filename, new_status):
    global global_history_map
    if not config.get("device_id") or not filename: return
    status_to_save = "finished" if new_status == "complete" else new_status
    current_date = datetime.now().astimezone().isoformat()
    url = f"{config['supabase_url']}/rest/v1/printer_files?device_id=eq.{config['device_id']}&filename=eq.{filename}"
    payload = {
        "last_print_status": status_to_save,
        "last_print_date": current_date
    }
    try:
        r = requests.patch(url, json=payload, headers=get_headers(), timeout=5)
        if r.status_code in [200, 204]:
            global_history_map[filename] = {
                "status": status_to_save,
                "date": current_date
            }
    except:
        pass

def upload_file_list(delay_start=False):
    global global_history_map
    if not config.get("device_id"): return
    if delay_start: time.sleep(2)
    if not sync_lock.acquire(blocking=False): return

    url_start = f"{config['supabase_url']}/rest/v1/rpc/set_sync_status"
    is_first_massive_sync = not config.get("has_full_history_sync", False)
    sync_ui_triggered = False

    try:
        host = config.get("moonraker_host", "127.0.0.1")
        port = config.get("moonraker_port", 7125)

        moonraker_ready = False
        limit = 2000 if is_first_massive_sync else 50

        for attempt in range(10):
            try:
                url_history = f"http://{host}:{port}/server/history/list?limit={limit}"
                r_hist = requests.get(url_history, timeout=10)
                if r_hist.status_code == 200:
                    jobs = r_hist.json().get("result", {}).get("jobs", [])
                    for job in reversed(jobs):
                        fname = job.get("filename")
                        status = job.get("status")
                        end_time = job.get("end_time")
                        if fname:
                            mapped_status = "finished" if status == "completed" else status
                            global_history_map[fname] = {
                                "status": mapped_status,
                                "date": datetime.fromtimestamp(end_time).isoformat() if end_time else None
                            }
                    moonraker_ready = True
                    break
            except:
                pass
            time.sleep(2)

        if not moonraker_ready:
            return

        existing_files_map = None
        for attempt in range(30):
            try:
                url_get_rpc = f"{config['supabase_url']}/rest/v1/rpc/get_device_file_index"
                r_exist = requests.post(url_get_rpc, json={"target_device_id": config['device_id']}, headers=get_headers(), timeout=5)

                if r_exist.status_code == 200:
                    existing_files_map = {}
                    data = r_exist.json()
                    if isinstance(data, list):
                        for row in data:
                            existing_files_map[row['filename']] = {
                                "modified": row.get("modified"),
                                "thumbnail_url": row.get("thumbnail_url"),
                                "last_print_status": row.get("last_print_status"),
                                "last_print_date": row.get("last_print_date")
                            }
                    break
            except:
                pass
            time.sleep(2)

        if existing_files_map is None:
            return

        if is_first_massive_sync:
            config["has_full_history_sync"] = True
            save_config()

        url_files = f"http://{host}:{port}/server/files/list?root=gcodes"
        r = requests.get(url_files, timeout=10)
        if r.status_code == 200:
            files_data = r.json().get("result", [])
            current_filenames_set = set()
            for f in files_data:
                fname = f.get("filename")
                if not fname: fname = f.get("path")
                if fname: current_filenames_set.add(fname)

            for db_filename in list(existing_files_map.keys()):
                if db_filename not in current_filenames_set:
                    if not sync_ui_triggered:
                        try:
                            requests.post(url_start, json={"target_device_id": config['device_id'], "sync_state": True}, headers=get_headers(), timeout=2)
                            sync_ui_triggered = True
                        except: pass

                    try:
                        url_cloud = f"{config['supabase_url']}/functions/v1/upload-thumbnail"
                        requests.delete(url_cloud, json={"filename": db_filename, "folder_id": config['device_id']},
                                        headers=get_headers(), timeout=5)

                        url_rpc_del = f"{config['supabase_url']}/rest/v1/rpc/delete_printer_file"
                        payload_del = {
                            "p_target_device_id": config['device_id'],
                            "p_filename": db_filename
                        }
                        requests.post(url_rpc_del, json=payload_del, headers=get_headers(), timeout=5)
                    except:
                        pass

            clean_list = []
            total_files = len(files_data)
            processed_files = 0
            last_reported_percent = 0

            try:
                url_prog = f"{config['supabase_url']}/rest/v1/rpc/update_sync_progress"
                requests.post(url_prog, json={"p_device_id": config['device_id'], "p_progress": 0}, headers=get_headers(), timeout=2)
            except:
                pass

            for f in files_data:
                processed_files += 1
                if total_files > 0:
                    current_percent = int((processed_files / total_files) * 100)
                    rounded_percent = (current_percent // 10) * 10

                    if processed_files == total_files:
                        rounded_percent = 100

                    if rounded_percent >= last_reported_percent + 10:
                        last_reported_percent = rounded_percent
                        try:
                            requests.post(url_prog, json={"p_device_id": config['device_id'], "p_progress": rounded_percent}, headers=get_headers(), timeout=2)
                        except:
                            pass

                filename = f.get("filename")
                if not filename: filename = f.get("path")
                current_modified_ts = f.get("modified")

                if filename in existing_files_map:
                    known = existing_files_map[filename]
                    known_ts = known["modified"]
                    if known_ts is not None:
                        diff = abs(float(known_ts) - float(current_modified_ts))
                        if diff < 2.0 and not is_first_massive_sync:
                            continue

                if not sync_ui_triggered:
                    try:
                        requests.post(url_start, json={"target_device_id": config['device_id'], "sync_state": True}, headers=get_headers(), timeout=2)
                        sync_ui_triggered = True
                    except: pass

                file_history = global_history_map.get(filename)
                if not file_history:
                    if filename in existing_files_map and existing_files_map[filename].get("last_print_status"):
                        file_history = {
                            "status": existing_files_map[filename]["last_print_status"],
                            "date": existing_files_map[filename]["last_print_date"]
                        }
                    else:
                        file_history = {"status": "idle", "date": None}

                file_obj = {
                    "path": f.get("path"),
                    "filename": filename,
                    "size": f.get("size"),
                    "modified": current_modified_ts,
                    "filament_used": 0, "filament_weight": 0, "estimated_time": 0, "layer_count": 0,
                    "object_height": 0, "layer_height": 0, "first_layer_height": 0,
                    "temp_bed": 0, "temp_nozzle": 0, "nozzle_diameter": 0.4,
                    "thumbnail_url": existing_files_map.get(filename, {}).get("thumbnail_url"),
                    "slicer_name": "Unknown",
                    "slicer_version": "",
                    "last_print_status": file_history["status"],
                    "last_print_date": file_history["date"],
                    "is_syncing": False
                }

                try:
                    meta_url = f"http://{host}:{port}/server/files/metadata?filename={filename}"
                    for attempt in range(10):
                        rm = requests.get(meta_url, timeout=2)
                        if rm.status_code == 200:
                            m = rm.json().get("result", {})
                            est_time = float(m.get("estimated_time", 0) or 0)
                            if est_time > 0 or file_obj["size"] < 100:
                                file_obj["filament_used"] = float(m.get("filament_total", 0) or 0)
                                file_obj["filament_weight"] = float(m.get("filament_weight_total", 0) or 0)
                                file_obj["estimated_time"] = est_time
                                file_obj["object_height"] = float(m.get("object_height", 0) or 0)
                                file_obj["layer_height"] = float(m.get("layer_height", 0) or 0)
                                file_obj["first_layer_height"] = float(m.get("first_layer_height", 0) or 0)
                                file_obj["nozzle_diameter"] = float(m.get("nozzle_diameter", 0.4) or 0.4)
                                file_obj["temp_bed"] = safe_int(m.get("first_layer_bed_temp"))
                                file_obj["temp_nozzle"] = safe_int(m.get("first_layer_extr_temp"))
                                file_obj["slicer_name"] = m.get("slicer", "Unknown")
                                file_obj["slicer_version"] = m.get("slicer_version", "")
                                if file_obj["layer_height"] > 0 and file_obj["object_height"] > 0:
                                    file_obj["layer_count"] = int(file_obj["object_height"] / file_obj["layer_height"])
                                thumbs = m.get("thumbnails", [])
                                if thumbs:
                                    thumbs.sort(key=lambda x: x.get("size", 0), reverse=True)
                                    best_thumb = thumbs[0]
                                    thumb_path = best_thumb.get("relative_path")
                                    if thumb_path:
                                        img_url = f"http://{host}:{port}/server/files/gcodes/{thumb_path}"
                                        img_resp = requests.get(img_url, timeout=3)
                                        if img_resp.status_code == 200:
                                            b64_string = base64.b64encode(img_resp.content).decode('utf-8')
                                            func_url = f"{config['supabase_url']}/functions/v1/upload-thumbnail"
                                            folder = config.get("device_id", "unknown_device")
                                            pl_func = {"imageBase64": b64_string, "filename": filename,
                                                       "folder_id": folder}
                                            r_func = requests.post(func_url, json=pl_func, headers=get_headers(),
                                                                   timeout=5)
                                            if r_func.status_code == 200:
                                                file_obj["thumbnail_url"] = r_func.json().get("url")
                                break
                        if attempt < 9: time.sleep(2)
                except:
                    pass
                clean_list.append(file_obj)

            if clean_list:
                url_rpc = f"{config['supabase_url']}/rest/v1/rpc/sync_printer_files"
                payload = {"target_device_id": config['device_id'], "file_list": clean_list}
                requests.post(url_rpc, json=payload, headers=get_headers(), timeout=20)
    finally:
        if sync_ui_triggered:
            try:
                requests.post(url_start, json={"target_device_id": config['device_id'], "sync_state": False},
                              headers=get_headers(), timeout=5)
            except:
                pass
        sync_lock.release()

def detect_and_upload_capabilities():
    if not config.get("device_id"): return
    host = config.get("moonraker_host", "127.0.0.1")
    port = config.get("moonraker_port", 7125)
    try:
        url_list = f"http://{host}:{port}/printer/objects/list"
        r_list = requests.get(url_list, timeout=5)
        objects = []
        if r_list.status_code == 200:
            objects = r_list.json().get("result", {}).get("objects", [])

        url_config = f"http://{host}:{port}/printer/objects/query?configfile&toolhead"
        r_conf = requests.get(url_config, timeout=5)

        kinematics = "cartesian"
        volume_x, volume_y, volume_z = 0, 0, 0

        if r_conf.status_code == 200:
            status_data = r_conf.json().get("result", {}).get("status", {})
            try:
                kinematics = status_data["configfile"]["settings"]["printer"]["kinematics"]
            except:
                pass
            try:
                axis_max = status_data["toolhead"]["axis_maximum"]
                volume_x = int(axis_max[0])
                volume_y = int(axis_max[1])
                volume_z = int(axis_max[2])
            except:
                pass

        caps = {
            "kinematics": kinematics,
            "volume_x": volume_x,
            "volume_y": volume_y,
            "volume_z": volume_z,
            "has_heated_bed": "heater_bed" in objects,
            "has_chamber_sensor": "temperature_sensor chamber" in objects or "heater_generic chamber" in objects,
            "has_chamber_heater": "heater_generic chamber" in objects,
            "has_lights": "output_pin caselight" in objects or any("neopixel" in obj for obj in objects),
            "leveling_method": "none",
            "has_bed_mesh": "bed_mesh" in objects,
            "tools_count": 1
        }
        if "quad_gantry_level" in objects:
            caps["leveling_method"] = "qgl"
        elif "z_tilt" in objects:
            caps["leveling_method"] = "z_tilt"
        elif "bed_screws" in objects or "screws_tilt_adjust" in objects:
            caps["leveling_method"] = "screws"

        url_rpc = f"{config['supabase_url']}/rest/v1/rpc/update_printer_capabilities"
        requests.post(url_rpc, json={"target_device_id": config['device_id'], "caps": caps}, headers=get_headers(), timeout=5)
    except:
        pass

def send_print_stats_success(duration):
    if not config.get("device_id") or not is_paired or duration < 60: return
    url = f"{config['supabase_url']}/rest/v1/rpc/increment_print_stats"
    try:
        requests.post(url, json={"row_id": config['device_id'], "duration": int(duration)}, headers=get_headers(),
                      timeout=5)
    except:
        pass

def send_history_point():
    if not config.get("device_id") or not is_paired: return
    url = f"{config['supabase_url']}/rest/v1/rpc/add_measurement"
    payload = {
        "p_device_id": config['device_id'],
        "p_bed": int(cache['temp_bed']) if cache['temp_bed'] > -100 else 0,
        "p_nozzle": int(cache['temp_nozzle']) if cache['temp_nozzle'] > -100 else 0,
        "p_target_bed": int(cache['target_bed']),
        "p_target_nozzle": int(cache['target_nozzle']),
        "p_chamber": int(cache['temp_chamber']),
        "p_pi": int(cache['temp_pi']),
        "p_mcu": int(cache['temp_mcu'])
    }
    try:
        requests.post(url, json=payload, headers=get_headers(), timeout=3)
    except:
        pass

def send_rpc_status_update(payload_data):
    if not config.get("device_id"): return
    url = f"{config['supabase_url']}/rest/v1/rpc/update_printer_status"
    try:
        r = requests.post(url, json={"target_device_id": config['device_id'], "payload": payload_data}, headers=get_headers(), timeout=5)
        if r.status_code not in [200, 204]:
            log(f"Refus de Supabase ({r.status_code}) : {r.text}")
    except Exception as e:
        log(f"Erreur réseau vers Supabase : {e}")

def ack_command_rpc(cmd_id):
    if not config.get("device_id"): return
    url = f"{config['supabase_url']}/rest/v1/rpc/acknowledge_command"
    try:
        requests.post(url, json={"target_id": cmd_id}, headers=get_headers(), timeout=5)
    except:
        pass

def cleanup_on_startup():
    load_config()
    if not config.get("device_id"): return
    try:
        url_get = f"{config['supabase_url']}/rest/v1/rpc/get_bridge_status"
        r = requests.post(url_get, json={"target_device_id": config['device_id']}, headers=get_headers(), timeout=5)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and len(data) > 0: data = data[0]
            if data.get("status") == "printing":
                send_rpc_status_update({"status": "offline", "current_session_time": 0})
    except:
        pass

def update_supabase(payload):
    if not config.get("device_id") or not is_paired:
        log("Envoi annulé : Bridge non appairé ou ID manquant.")
        return
    log(f"📡 Envoi des données (Statut: {payload.get('status')})...")
    send_rpc_status_update(payload)
    threading.Thread(target=send_rpc_status_update, args=(payload,), daemon=True).start()

def calculate_final_progress(c, meta_time, meta_filament):
    if c["status"] != "printing": return c.get("progress", 0)
    if c.get("raw_m73_progress", 0) > 0: return c["raw_m73_progress"]
    if c.get("raw_file_progress", 0) > 0: return c["raw_file_progress"]
    if meta_filament > 0 and c.get("filament_used", 0) > 0:
        return min(99, int((c["filament_used"] / meta_filament) * 100))
    if meta_time > 0 and c.get("print_duration", 0) > 0:
        return min(99, int((c["print_duration"] / meta_time) * 100))
    return c.get("raw_file_progress", 0)

def process_data(status_data):
    global cache, last_sent_cache, last_heartbeat, last_z_update_ts, last_history_ts, force_full_sync, last_print_update_ts, current_file_metadata_time, current_file_metadata_filament, last_api_call_ts, is_suspended_billing, sync_interval
    if is_suspended_billing: return
    if "webhooks" in status_data:
        wh = status_data["webhooks"]
        if "state" in wh:
            cache["raw_webhooks_state"] = wh["state"]
    if "print_stats" in status_data:
        ps = status_data["print_stats"]
        if "state" in ps: cache["raw_print_state"] = ps["state"]
        if "filename" in ps and ps["filename"]:
            if cache["filename"] != ps["filename"]:
                cache["filename"] = ps["filename"]
                threading.Thread(target=fetch_current_file_metadata, args=(cache["filename"],), daemon=True).start()
        if "print_duration" in ps: cache["print_duration"] = int(ps["print_duration"])
        if "filament_used" in ps: cache["filament_used"] = round(ps["filament_used"], 2)
        t_duration_api = ps.get("total_duration", 0)
        if current_file_metadata_time == 0 and t_duration_api > 0:
            cache["time_total"] = int(t_duration_api)
        elif current_file_metadata_time > 0:
            cache["time_total"] = int(current_file_metadata_time)
    if "virtual_sdcard" in status_data and "progress" in status_data["virtual_sdcard"]:
        cache["raw_file_progress"] = int(status_data["virtual_sdcard"]["progress"] * 100)
    if "display_status" in status_data:
        ds = status_data["display_status"]
        if "progress" in ds:
            cache["raw_m73_progress"] = int(ds["progress"] * 100)
        if "message" in ds:
            msg = ds["message"]
            if msg is not None and str(msg).strip() != "":
                msg_str = str(msg)
                if ">> PolyOrchestra" in msg_str or ">> Factory Reset" in msg_str:
                    cache["status_message"] = ""
                else:
                    cache["status_message"] = msg_str
            else:
                cache["status_message"] = ""

    ps_state = cache["raw_print_state"] if cache["raw_print_state"] else "ready"
    if ps_state == "": ps_state = "ready"
    if cache.get("raw_webhooks_state") not in ["ready", ""]:
        cache["status"] = "error"
    else:
        if ps_state in ["standby", "ready"]:
            if cache.get("status_message") != "":
                cache["status"] = "busy"
            else:
                cache["status"] = ps_state
        else:
            cache["status"] = ps_state

    if "toolhead" in status_data:
        th = status_data["toolhead"]
        if "max_velocity" in th:
            cache["print_speed"] = int(th["max_velocity"])
        if "max_accel" in th:
            cache["accel"] = int(th["max_accel"])
        if "minimum_cruise_ratio" in th:
            cache["minimum_cruise_ratio"] = float(th["minimum_cruise_ratio"])
        if "square_corner_velocity" in th:
            cache["velocity_scv"] = float(th["square_corner_velocity"])

    if "heater_bed" in status_data:
        if "temperature" in status_data["heater_bed"]: cache["temp_bed"] = status_data["heater_bed"]["temperature"]
        if "target" in status_data["heater_bed"]: cache["target_bed"] = int(round(status_data["heater_bed"]["target"]))
        if "pressure_advance" in status_data["extruder"]:
            cache["pressure_advance"] = round(float(status_data["extruder"]["pressure_advance"]), 3)
        if "smooth_time" in status_data["extruder"]:
            cache["smooth_time"] = round(float(status_data["extruder"]["smooth_time"]), 3)
    if "extruder" in status_data:
        if "temperature" in status_data["extruder"]: cache["temp_nozzle"] = status_data["extruder"]["temperature"]
        if "target" in status_data["extruder"]: cache["target_nozzle"] = int(round(status_data["extruder"]["target"]))
    if "temperature_sensor chamber" in status_data:
        cache["temp_chamber"] = int(round(status_data["temperature_sensor chamber"]["temperature"]))
    elif "heater_generic chamber" in status_data:
        cache["temp_chamber"] = int(round(status_data["heater_generic chamber"]["temperature"]))
    if "fan" in status_data and "speed" in status_data["fan"]: cache["fan_speed"] = int(
        status_data["fan"]["speed"] * 100)
    if "output_pin caselight" in status_data:
        val = status_data["output_pin caselight"].get("value", 0)
        cache["light_intensity"] = int(val * 100)
    if "gcode_move" in status_data:
        gm = status_data["gcode_move"]
        if "speed_factor" in gm: cache["speed_factor"] = int(gm["speed_factor"] * 100)
        if "extrude_factor" in gm: cache["flow_factor"] = int(gm["extrude_factor"] * 100)
        if "gcode_position" in gm:
            cache["pos_x"] = round(gm["gcode_position"][0], 2)
            cache["pos_y"] = round(gm["gcode_position"][1], 2)
            cache["pos_z"] = round(gm["gcode_position"][2], 2)

    if cache["time_total"] > 0:
        cache["time_remaining"] = max(0, int(cache["time_total"] - cache["print_duration"]))
    else:
        cache["time_remaining"] = 0

    cache["progress"] = calculate_final_progress(cache, current_file_metadata_time, current_file_metadata_filament)
    now = time.time()
    is_critical_change = False
    if cache["status"] != last_sent_cache["status"]: is_critical_change = True
    if cache.get("status_message") != last_sent_cache.get("status_message"): is_critical_change = True
    if cache["filename"] != last_sent_cache["filename"]: is_critical_change = True

    should_send = is_critical_change
    if force_full_sync: should_send = True; force_full_sync = False
    if (now - last_heartbeat) > 60: should_send = True

    is_moving_while_idle = (cache["status"] != "printing") and (
            abs(cache.get("pos_x", 0) - last_sent_cache.get("pos_x", 0)) >= 0.1 or
            abs(cache.get("pos_y", 0) - last_sent_cache.get("pos_y", 0)) >= 0.1 or
            abs(cache.get("pos_z", 0) - last_sent_cache.get("pos_z", 0)) >= 0.1
    )

    has_minor_changes = (
            abs(cache.get("temp_bed", 0) - last_sent_cache.get("temp_bed", 0)) >= 0.8 or
            abs(cache.get("temp_nozzle", 0) - last_sent_cache.get("temp_nozzle", 0)) >= 0.8 or
            cache.get("fan_speed") != last_sent_cache.get("fan_speed") or
            cache.get("light_intensity") != last_sent_cache.get("light_intensity") or
            cache.get("progress") != last_sent_cache.get("progress") or
            cache.get("print_speed") != last_sent_cache.get("print_speed") or
            cache.get("accel") != last_sent_cache.get("accel") or
            cache.get("velocity_scv") != last_sent_cache.get("velocity_scv") or
            cache.get("minimum_cruise_ratio", 0.5) != last_sent_cache.get("minimum_cruise_ratio", 0.5) or
            cache.get("speed_factor") != last_sent_cache.get("speed_factor") or
            cache.get("flow_factor") != last_sent_cache.get("flow_factor") or
            abs(cache.get("pressure_advance", 0.0) - last_sent_cache.get("pressure_advance", 0.0)) >= 0.001 or
            abs(cache.get("smooth_time", 0.04) - last_sent_cache.get("smooth_time", 0.04)) >= 0.005 or
            is_moving_while_idle
    )

    if has_minor_changes and (now - last_api_call_ts) >= sync_interval:
        should_send = True

    if should_send:
        if not is_critical_change and (now - last_api_call_ts) < 2.0: return
        payload = {
            "status": cache["status"],
            "progress": cache["progress"],
            "time_remaining": cache["time_remaining"],
            "time_total": cache["time_total"],
            "current_session_time": cache["print_duration"],
            "filename": cache["filename"],
            "temperature_bed": int(round(cache["temp_bed"])),
            "temperature_nozzle": int(round(cache["temp_nozzle"])),
            "temperature_chamber": cache["temp_chamber"],
            "fan_speed": cache["fan_speed"],
            "light_intensity": cache.get("light_intensity", 0),
            "pos_x": cache.get("pos_x", 0.0),
            "pos_y": cache.get("pos_y", 0.0),
            "pos_z": cache.get("pos_z", 0.0),
            "status_message": cache.get("status_message", ""),
            "print_speed": cache["print_speed"],
            "accel": cache["accel"],
            "minimum_cruise_ratio": cache.get("minimum_cruise_ratio", 0.5),
            "velocity_scv": cache["velocity_scv"],
            "speed_factor": cache.get("speed_factor", 100),
            "flow_factor": cache.get("flow_factor", 100),
            "pressure_advance": cache.get("pressure_advance", 0.0),
            "smooth_time": cache.get("smooth_time", 0.04)
        }
        update_supabase(payload)

        if cache["status"] != last_sent_cache["status"]:
            if cache["status"] in ["complete", "cancelled", "error"]:
                threading.Thread(
                    target=update_file_status_in_db,
                    args=(cache["filename"], cache["status"]),
                    daemon=True
                ).start()

        if cache["status"] == "complete" and last_sent_cache["status"] != "complete":
            threading.Thread(target=send_print_stats_success, args=(cache["print_duration"],), daemon=True).start()

        last_sent_cache = cache.copy()
        last_api_call_ts = now
        last_heartbeat = now

        if (now - last_history_ts) > 15:
            threading.Thread(target=send_history_point, daemon=True).start()
            last_history_ts = now

def handle_command(cmd_row):
    global cache, last_file_action_ts
    cmd = cmd_row.get("command", "").upper()
    pl = cmd_row.get("payload", {})
    if cmd == "RESET_FACTORY":
        send_gcode("M117 >> Factory Reset...")
        send_rpc_status_update({"status": "deleted"})
        ack_command_rpc(cmd_row.get("id"))
        time.sleep(2.0)
        try:
            keep_url = config.get("supabase_url")
            keep_key = config.get("supabase_key")
            keep_host = config.get("moonraker_host", "127.0.0.1")
            keep_port = config.get("moonraker_port", 7125)
            if keep_url and keep_key:
                with open(CONFIG_PATH, "w") as f:
                    json.dump({"supabase_url": keep_url, "supabase_key": keep_key, "moonraker_host": keep_host,
                               "moonraker_port": keep_port}, f, indent=4)
                    f.flush()
                    os.fsync(f.fileno())
            time.sleep(2)
            os._exit(1)
        except:
            return
    elif cmd == "DELETE_FILE":
        last_file_action_ts = time.time()
        filename = pl.get("filename")
        if filename:
            host = config.get("moonraker_host", "127.0.0.1")
            port = config.get("moonraker_port", 7125)
            try:
                requests.delete(f"http://{host}:{port}/server/files/gcodes/{filename}", timeout=5)
                url_cloud = f"{config['supabase_url']}/functions/v1/upload-thumbnail"
                requests.delete(url_cloud, json={"filename": filename, "folder_id": config['device_id']},
                                headers=get_headers(), timeout=5)
                url_rpc_del = f"{config['supabase_url']}/rest/v1/rpc/delete_printer_file"
                payload_del = {
                    "p_target_device_id": config['device_id'],
                    "p_filename": filename
                }
                requests.post(url_rpc_del, json=payload_del, headers=get_headers(), timeout=5)
            except:
                pass
    elif cmd == "RENAME_FILE":
        last_file_action_ts = time.time()
        old_name = pl.get("old_filename")
        new_name = pl.get("new_filename")

        if old_name and new_name:
            if not new_name.lower().endswith(".gcode"): new_name += ".gcode"
            host = config.get("moonraker_host", "127.0.0.1")
            port = config.get("moonraker_port", 7125)
            check_url = f"http://{host}:{port}/server/files/metadata?filename={new_name}"
            try:
                r_check = requests.get(check_url, timeout=2)
                if r_check.status_code != 200:

                    url_rpc_rename = f"{config['supabase_url']}/rest/v1/rpc/rename_printer_file"
                    payload_rename = {
                        "p_target_device_id": config['device_id'],
                        "p_old_filename": old_name,
                        "p_new_filename": new_name
                    }
                    requests.post(url_rpc_rename, json=payload_rename, headers=get_headers(), timeout=5)

                    move_url = f"http://{host}:{port}/server/files/move"
                    payload_move = {"source": f"gcodes/{old_name}", "dest": f"gcodes/{new_name}"}
                    requests.post(move_url, data=payload_move, timeout=5)
            except:
                pass
    elif cmd == "SET_TEMP_BED":
        send_gcode(f"M140 S{int(pl.get('value', 0))}")
    elif cmd == "SET_TEMP_NOZZLE":
        send_gcode(f"M104 S{int(pl.get('value', 0))}")
    elif cmd == "PID_CALIBRATE":
        global calibrating_profile_id
        calibrating_profile_id = pl.get("profile_id")
        heater = pl.get("heater", "extruder")
        target = int(pl.get("target", 210))
        fan_speed = int(pl.get("fan_speed", 100))
        z_height = int(pl.get("z_height", 10))
        script = []
        script.append("G28")
        script.append("G90")
        script.append(f"G1 Z{z_height} F1500")
        if heater == "extruder" and fan_speed > 0:
            pwm_val = int(fan_speed * 2.55)
            script.append(f"M106 S{pwm_val}")
        script.append(f"PID_CALIBRATE HEATER={heater} TARGET={target}")
        if heater == "extruder" and fan_speed > 0:
            script.append("M106 S0")
        send_gcode("\n".join(script))
    elif cmd == "PAUSE":
        send_gcode("PAUSE")
    elif cmd == "RESUME":
        send_gcode("RESUME")
    elif cmd == "CANCEL":
        send_gcode("CANCEL_PRINT")
    elif cmd == "HOME":
        axes = pl.get("axes", "").upper()
        send_gcode(f"G28 {axes}" if axes else "G28")
        cache["status_message"] = "G28"
    elif cmd == "QGL":
        send_gcode("QUAD_GANTRY_LEVEL")
        cache["status_message"] = "QUAD_GANTRY_LEVEL"
    elif cmd == "Z_TILT":
        send_gcode("Z_TILT_ADJUST")
        cache["status_message"] = "Z_TILT_ADJUST"
    elif cmd == "SCREWS_TILT":
        send_gcode("SCREWS_TILT_CALCULATE")
        cache["status_message"] = "SCREWS_TILT_CALCULATE"
    elif cmd == "BED_MESH":
        send_gcode("BED_MESH_CALIBRATE")
        cache["status_message"] = "BED_MESH_CALIBRATE"
    elif cmd == "MOVE":
        axis = pl.get("axis", "").upper()
        if axis in ["X", "Y", "Z"]: send_gcode(f"G91\nG1 {axis}{pl.get('value', 0)} F{pl.get('speed', 3000)}\nG90")
    elif cmd == "MOVE_ABSOLUTE":
        axis = pl.get("axis", "").upper()
        pos = pl.get("position", pl.get("value", 0))
        speed = pl.get("speed", 3000)
        if axis in ["X", "Y", "Z"]:
            send_gcode(f"G90\nG0 {axis}{pos} F{speed}")
    elif cmd == "EXTRUDE":
        send_gcode(f"M83\nG1 E{pl.get('amount', 0)} F{pl.get('speed', 300)}")
    elif cmd == "BABYSTEP":
        send_gcode(f"SET_GCODE_OFFSET Z_ADJUST={pl.get('value', 0.0)} MOVE=1")
    elif cmd == "SAVE_Z_OFFSET":
        send_gcode("Z_OFFSET_APPLY_PROBE\nSAVE_CONFIG")
    elif cmd == "SET_FAN":
        send_gcode(f"M106 S{int(pl.get('value', 0) * 2.55)}")
    elif cmd == "SET_LIGHT":
        light_val = float(pl.get('value', 0)) / 100.0
        send_gcode(f"SET_PIN PIN=caselight VALUE={light_val:.2f}")
    elif cmd == "SET_SPEED":
        send_gcode(f"M220 S{pl.get('value', 100)}")
    elif cmd == "SET_FLOW":
        send_gcode(f"M221 S{pl.get('value', 100)}")
    elif cmd == "SET_PA":
        parts = []
        adv_val = pl.get('advance', pl.get('pressure_advance', pl.get('pressureAdvance')))
        if adv_val is not None:
            parts.append(f"ADVANCE={adv_val}")
        smooth_val = pl.get('smooth', pl.get('smooth_time', pl.get('smoothTime')))
        if smooth_val is not None:
            parts.append(f"SMOOTH_TIME={smooth_val}")
        if parts:
            send_gcode(f"SET_PRESSURE_ADVANCE {' '.join(parts)}")
    elif cmd == "SET_LIMITS":
        parts = []
        if pl.get("velocity"): parts.append(f"VELOCITY={int(pl.get('velocity'))}")
        if pl.get("accel"): parts.append(f"ACCEL={int(pl.get('accel'))}")
        if pl.get("minimum_cruise_ratio") is not None: parts.append(f"MINIMUM_CRUISE_RATIO={float(pl.get('minimum_cruise_ratio'))}")
        if pl.get("scv"): parts.append(f"SQUARE_CORNER_VELOCITY={pl.get('scv')}")
        if parts: send_gcode(f"SET_VELOCITY_LIMIT {' '.join(parts)}")
    elif cmd == "START_PRINT":
        filename = pl.get("filename")
        if filename:
            send_gcode(f'SDCARD_PRINT_FILE FILENAME="{filename}"')
    elif cmd == "GCODE":
        if pl.get("value"): send_gcode(pl.get("value"))

def refresh_moonraker_data(socket):
    socket.send(json.dumps({"jsonrpc": "2.0", "method": "printer.objects.query", "params": {"objects": {
        "print_stats": None, "virtual_sdcard": None, "heater_bed": None, "extruder": None, "fan": None,
        "gcode_move": None, "toolhead": None,
        "output_pin caselight": None, "temperature_sensor chamber": None, "heater_generic chamber": None,
        "temperature_sensor raspberry_pi": None, "temperature_sensor mcu": None, "display_status": None,
        "webhooks": None
    }}, "id": random.randint(1000, 9999)}))

def check_commands_loop():
    global last_user_active_ts, is_paired, force_full_sync, is_suspended_billing, sync_interval
    while True:
        sleep_time = 5.0
        try:
            if config.get("device_id"):
                headers_public = {"apikey": config['supabase_key'], "Authorization": f"Bearer {config['supabase_key']}",
                                  "Content-Type": "application/json"}
                url_rpc_status = f"{config['supabase_url']}/rest/v1/rpc/get_bridge_status"
                r_status = requests.post(url_rpc_status, json={"target_device_id": config['device_id']},
                                         headers=headers_public, timeout=2)
                if r_status.status_code == 200:
                    data = r_status.json()
                    if isinstance(data, list) and len(data) > 0: data = data[0]
                    is_suspended_billing = data.get('is_suspended', False)
                    server_interval = data.get('refresh_interval')
                    if server_interval is not None:
                        sync_interval = max(2.0, float(server_interval))
                    if data.get('status') == "ready_to_pair":
                        is_paired = False
                    else:
                        if not is_paired:
                            send_gcode("RESPOND TYPE=command MSG='action:prompt_end'")
                            send_gcode("M117 >> PolyOrchestra Linked!")
                            reset_cache_values()
                            detect_and_upload_capabilities()
                            is_paired = True
                            force_full_sync = True
                            if ws_app and ws_app.sock and ws_app.sock.connected:
                                refresh_moonraker_data(ws_app)
                                threading.Thread(target=upload_file_list, daemon=True).start()
                    new_secret = data.get('api_secret')
                    if new_secret and new_secret != config.get('api_secret'):
                        config['api_secret'] = new_secret
                        save_config()
                    if data.get('last_user_interaction'):
                        try:
                            last_user_active_ts = datetime.fromisoformat(
                                data.get('last_user_interaction').replace("Z", "+00:00")).timestamp()
                        except:
                            pass
                is_turbo = (time.time() - last_user_active_ts) < 30
                sleep_time = 0.5 if is_turbo else 5.0
                url_rpc_get = f"{config['supabase_url']}/rest/v1/rpc/get_pending_commands"
                r_queue = requests.post(url_rpc_get, json={"target_device_id": config['device_id']},
                                        headers=get_headers(), timeout=2)
                for cmd in r_queue.json():
                    handle_command(cmd)
                    if cmd.get("command") != "RESET_FACTORY": ack_command_rpc(cmd['id'])
                    sleep_time = 0.1
                if time.time() - last_api_call_ts >= sync_interval:
                    process_data({})
        except Exception as e:
            log(f"Erreur dans la boucle de vérification : {e}")
            sleep_time = 10.0
        time.sleep(sleep_time)

def on_open(ws):
    ws.send(json.dumps({"jsonrpc": "2.0", "method": "printer.objects.subscribe", "params": {"objects": {
        "print_stats": None, "virtual_sdcard": None, "heater_bed": None, "extruder": None, "fan": None,
        "gcode_move": None, "toolhead": None,
        "output_pin caselight": None, "temperature_sensor chamber": None, "heater_generic chamber": None,
        "temperature_sensor raspberry_pi": None, "temperature_sensor mcu": None, "display_status": None,
        "webhooks": None
    }}, "id": 1}))
    if is_paired:
        threading.Thread(target=lambda: (time.sleep(1), refresh_moonraker_data(ws)), daemon=True).start()
        threading.Thread(target=detect_and_upload_capabilities, daemon=True).start()
        threading.Thread(target=upload_file_list, daemon=True).start()
    if pending_code:
        def show_code():
            time.sleep(3)
            full_script = f"M117 Code: {pending_code}\nRESPOND TYPE=command MSG=\"action:prompt_begin PolyOrchestra\"\nRESPOND TYPE=command MSG=\"action:prompt_text Code: {pending_code}\"\nRESPOND TYPE=command MSG=\"action:prompt_show\""
            if ws_app and ws_app.sock and ws_app.sock.connected:
                ws_app.send(json.dumps(
                    {"jsonrpc": "2.0", "method": "printer.gcode.script", "params": {"script": full_script}, "id": 999}))

        threading.Thread(target=show_code, daemon=True).start()

def on_message(ws, message):
    global calibrating_profile_id, last_file_action_ts
    try:
        response = json.loads(message)
        if "result" in response and "status" in response["result"]:
            process_data(response["result"]["status"])
        elif "method" in response and "notify_status_update" in response["method"]:
            if len(response["params"]) > 0: process_data(response["params"][0])
        elif "method" in response and response["method"] == "notify_filelist_changed":
            if time.time() - last_file_action_ts < 5.0:
                pass
            else:
                threading.Thread(target=lambda: upload_file_list(delay_start=True), daemon=True).start()
        elif "method" in response and response["method"] == "notify_gcode_response":
            msg_text = response["params"][0]
            if "PID parameters:" in msg_text and calibrating_profile_id:
                match = re.search(r"pid_Kp=([\d.]+)\s+pid_Ki=([\d.]+)\s+pid_Kd=([\d.]+)", msg_text)
                if match:
                    kp = float(match.group(1))
                    ki = float(match.group(2))
                    kd = float(match.group(3))
                    url = f"{config['supabase_url']}/rest/v1/rpc/save_pid_result"
                    payload = {"p_device_id": config['device_id'], "p_profile_id": calibrating_profile_id, "p_kp": kp,
                               "p_ki": ki, "p_kd": kd}
                    requests.post(url, json=payload, headers=get_headers())
                    calibrating_profile_id = None
                    send_gcode(f"M117 Config Saved to PolyOrchestra!")
    except Exception as e:
        log(f"Erreur interceptée dans on_message : {e}")

def connect_to_moonraker():
    global ws_app
    load_config()
    host = config.get("moonraker_host")
    port = config.get("moonraker_port", 7125)
    if not host or "192.168.1.XX" in host: return
    threading.Thread(target=check_commands_loop, daemon=True).start()
    while True:
        try:
            def on_error(ws, error):
                pass

            def on_close(ws, close_status_code, close_msg):
                pass

            ws_app = websocket.WebSocketApp(
                f"ws://{host}:{port}/websocket",
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close
            )
            ws_app.run_forever(ping_interval=10, ping_timeout=5)
        except:
            pass
        time.sleep(5)

if __name__ == "__main__":
    if ensure_registration():
        cleanup_on_startup()
        detect_and_upload_capabilities()
        connect_to_moonraker()