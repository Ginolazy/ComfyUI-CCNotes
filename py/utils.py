## ComfyUI/custom_nodes/CCNotes/py/utils.py
import torch
import numpy as np
import folder_paths
import os
import random
import server
import threading
from aiohttp import web
from nodes import SaveImage
from .utility.type_utility import (any_type, handle_error, handle_error_safe)
from .utility.image_utility import (
    generate_preview_images,
    flatten_input_values,
    generate_text_previews,
    create_rgba_from_image_mask,
    generate_editable_images,
    save_images_for_preview,
    send_preview_event,
    composite_image_with_color
)

PAUSE_REQUESTS = {}
MAX_FLOW_PORTS = 10

@server.PromptServer.instance.routes.post("/ccnotes/resume_pause")
async def resume_pause(request):
    post = await request.json()
    node_id = str(post.get("node_id"))
    action = post.get("action", "continue")
    edited_text = post.get("edited_text", None)

    images = post.get("images", None)
    if node_id in PAUSE_REQUESTS:
        PAUSE_REQUESTS[node_id]["action"] = action
        if edited_text is not None:
            PAUSE_REQUESTS[node_id]["edited_text"] = edited_text
        if images is not None:
            PAUSE_REQUESTS[node_id]["images"] = images
        PAUSE_REQUESTS[node_id]["event"].set()
        return web.json_response({"status": "success"})
    return web.json_response({"status": "ignored"}, status=200)

class PauseMixin:
    @classmethod
    def pause_is_changed(cls, action="Pause", **kwargs):
        if isinstance(action, list):
            action = action[0] if action else "Pause"
        if action == "Pause":
            return float("NaN")
        return 0

    def _handle_pause(self, unique_id_str, node_type, event_data, result_data, process_text_edit=False, **kwargs):
        input_dir = folder_paths.get_input_directory()
        clipspace_dir = os.path.join(input_dir, "clipspace")
        existing_files = set()
        
        if os.path.exists(clipspace_dir):
            try:
                for f in os.listdir(clipspace_dir):
                    if f.startswith("clipspace-mask-") and f.endswith(".png"):
                        existing_files.add(f)
            except Exception:
                pass

        if unique_id_str not in PAUSE_REQUESTS:
            PAUSE_REQUESTS[unique_id_str] = {
                "event": threading.Event(),
                "action": "continue",
                "existing_files": existing_files
            }
        
        # Always update existing_files when entering pause
        PAUSE_REQUESTS[unique_id_str]["existing_files"] = existing_files
        
        pause_data = PAUSE_REQUESTS[unique_id_str]
        pause_data["event"].clear()

        server.PromptServer.instance.send_sync("ccnotes.node_event", {
            "node_id": unique_id_str,
            "node_type": node_type,
            **event_data
        })

        pause_data["event"].wait()
        action = pause_data["action"]
        edited_text = pause_data.get("edited_text", None)
        # Pass image data to caller
        kwargs["pause_data"] = pause_data

        if process_text_edit and edited_text:
            result_data = self._apply_text_edits(result_data, edited_text, **kwargs)

        # Inject images into result_data if present (critical for mask updates)
        if "images" in pause_data:
            result_data["images"] = pause_data["images"]

        if unique_id_str in PAUSE_REQUESTS:
            del PAUSE_REQUESTS[unique_id_str]

        return result_data, action

    def _apply_text_edits(self, result_data, edited_text, **kwargs):
        input_keys = sorted([k for k in kwargs.keys() if k.startswith("input_")],
                           key=lambda x: int(x.split("_")[1]))
        input_values = [kwargs[k] for k in input_keys]
        actual_returns = list(input_values)

        if isinstance(edited_text, str):
            edited_text = [edited_text]

        if isinstance(edited_text, list):
            edit_idx = 0
            for i, port_vals in enumerate(actual_returns):
                new_port_vals = list(port_vals) if isinstance(port_vals, list) else [port_vals]
                modified_port = False

                for j, val in enumerate(new_port_vals):
                    if isinstance(val, str):
                        if edit_idx < len(edited_text):
                            new_port_vals[j] = edited_text[edit_idx]
                            edit_idx += 1
                            modified_port = True

                if modified_port:
                    actual_returns[i] = new_port_vals

        padding_count = MAX_FLOW_PORTS - len(actual_returns)
        if padding_count > 0:
            result_data["result"] = tuple(actual_returns) + ([],) * padding_count
        else:
            result_data["result"] = tuple(actual_returns[:MAX_FLOW_PORTS])

        return result_data

class AnyPause(PauseMixin):
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "action": (["Pause", "Pass", "Cancel"], {"default": "Pause"}),
            },
            "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO", "unique_id": "UNIQUE_ID"},
        }

    RETURN_TYPES = (any_type,) * MAX_FLOW_PORTS
    RETURN_NAMES = tuple(f"output_{i}" for i in range(1, MAX_FLOW_PORTS + 1))
    FUNCTION = "process_pause"
    OUTPUT_NODE = True
    CATEGORY = "CCNotes/Utils"
    INPUT_IS_LIST = True
    OUTPUT_IS_LIST = (True,) * MAX_FLOW_PORTS
    IS_CHANGED = PauseMixin.pause_is_changed

    def process_pause(self, action, prompt=None, extra_pnginfo=None, unique_id=None, **kwargs):
        action = action[0] if isinstance(action, list) and action else "Pause"
        unique_id_str = str(unique_id[0] if isinstance(unique_id, list) and unique_id else unique_id)

        input_keys = sorted([k for k in kwargs.keys() if k.startswith("input_")],
                           key=lambda x: int(x.split("_")[1]))
        input_values = [kwargs[k] for k in input_keys]
        actual_returns = list(input_values)

        padding_count = MAX_FLOW_PORTS - len(actual_returns)
        if padding_count > 0:
            result_tuple = tuple(actual_returns) + ([],) * padding_count
        else:
            result_tuple = tuple(actual_returns[:MAX_FLOW_PORTS])

        result_data = {"ui": {}, "result": result_tuple}

        if action == "Pass":
            server.PromptServer.instance.send_sync("ccnotes.node_event", {
                "node_id": unique_id_str,
                "node_type": "pause",
                "action": "pass"
            })
            return result_data

        if action == "Cancel":
            return result_data

        result_data, action = self._handle_pause(
            unique_id_str,
            "pause",
            {"action": "pause"},
            result_data,
            process_text_edit=False,
            **kwargs
        )

        return result_data

class AnyPreview(SaveImage):
    def __init__(self):
        self.output_dir = folder_paths.get_temp_directory()
        self.type = "temp"
        self.prefix_append = "_temp_" + ''.join(random.choice("abcdefghijklmnopqrstupvxyz") for x in range(5))
        self.compress_level = 4

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "preview_text": ("STRING", {"default": "", "multiline": True, "forceInput": False}),
            },
            "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO", "unique_id": "UNIQUE_ID"},
        }

    RETURN_TYPES = (any_type,) * MAX_FLOW_PORTS
    RETURN_NAMES = tuple(f"output_{i}" for i in range(1, MAX_FLOW_PORTS + 1))
    FUNCTION = "process_preview"
    OUTPUT_NODE = True
    CATEGORY = "CCNotes/Utils"
    INPUT_IS_LIST = True
    OUTPUT_IS_LIST = (True,) * MAX_FLOW_PORTS

    @classmethod
    def IS_CHANGED(s, **kwargs):
        return 0

    def process_preview(self, prompt=None, extra_pnginfo=None, unique_id=None, **kwargs):
        unique_id_str = str(unique_id[0] if isinstance(unique_id, list) and unique_id else unique_id)

        input_keys = sorted([k for k in kwargs.keys() if k.startswith("input_")], key=lambda x: int(x.split("_")[1]))
        input_values = [kwargs[k] for k in input_keys]
        actual_returns = list(input_values)

        # Flatten input values using utility function
        flat_input_values = flatten_input_values(input_values)

        # Generate preview images and save using utility function
        preview_images_list = generate_preview_images(flat_input_values)
        frontend_data = {}

        if preview_images_list:
            all_saved_images, _ = save_images_for_preview(self, preview_images_list, "CCNotes_preview")
            frontend_data["images"] = all_saved_images

        # Generate text previews using utility function
        text_previews = generate_text_previews(input_values)
        if text_previews:
            frontend_data["text"] = text_previews

        padding_count = MAX_FLOW_PORTS - len(actual_returns)
        if padding_count > 0:
            result_tuple = tuple(actual_returns) + ([],) * padding_count
        else:
            result_tuple = tuple(actual_returns[:MAX_FLOW_PORTS])

        # Send preview event using utility function
        send_preview_event(unique_id_str, frontend_data, "preview")

        return {"ui": frontend_data, "result": result_tuple}

class AnyPreviewPause(AnyPreview, PauseMixin):
    def __init__(self):
        super().__init__()

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "action": (["Pause", "Pass", "Cancel"], {"default": "Pause"}),
            },
            "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO", "unique_id": "UNIQUE_ID"},
        }

    FUNCTION = "process_any"
    IS_CHANGED = PauseMixin.pause_is_changed

    def process_any(self, action, prompt=None, extra_pnginfo=None, unique_id=None, **kwargs):
        action = action[0] if isinstance(action, list) and action else "Pause"
        unique_id_str = str(unique_id[0] if isinstance(unique_id, list) and unique_id else unique_id)

        # Do not use process_preview of parent class, implement manually to support RGBA preview
        input_keys = sorted([k for k in kwargs.keys() if k.startswith("input_")], key=lambda x: int(x.split("_")[1]))
        input_values = [kwargs[k] for k in input_keys]
        actual_returns = list(input_values)

        # Flatten input values using utility function
        flat_input_values = flatten_input_values(input_values)

        # Generate editable RGBA images and save using utility function
        editable_images_list = generate_editable_images(flat_input_values)
        frontend_data = {}
        saved_filenames = []
        
        if editable_images_list:
            all_saved_images, saved_filenames = save_images_for_preview(
                self, editable_images_list, "CCNotes_mask", collect_filenames=True
            )
            frontend_data["images"] = all_saved_images

        # Generate text previews using utility function
        text_previews = generate_text_previews(input_values)
        if text_previews:
            frontend_data["text"] = text_previews

        padding_count = MAX_FLOW_PORTS - len(actual_returns)
        if padding_count > 0:
            result_tuple = tuple(actual_returns) + ([],) * padding_count
        else:
            result_tuple = tuple(actual_returns[:MAX_FLOW_PORTS])

        result = {"ui": frontend_data, "result": result_tuple}

        if action == "Pass":
            # Send preview event using utility function
            send_preview_event(unique_id_str, result.get("ui", {}), "preview_pause", action="pass")
            return result

        if action == "Cancel":
            return result

        result, action = self._handle_pause(
            unique_id_str,
            "preview_pause",
            {"action": "pause", "data": result.get("ui", {})},
            result,
            process_text_edit=True,
            **kwargs
        )

        if action == "cancel":
            return result

        # Check if we have updated image information from frontend
        # Priority: Check top-level 'images' key first (populated by resume_pause)
        updated_images = result.get("images", None)
        if updated_images is None:
             updated_images = result.get("ui", {}).get("images", None)
        
        # If frontend provided image list, use it to update masks
        if updated_images and isinstance(updated_images, list):
            mask_updated = False
            updated_returns = list(actual_returns)
            
            # Map flattened image list to port indices
            # First pass: Identify which ports contain images and masks to map array index to port index
            current_img_idx = 0
            port_structure = [] # List of {img_idx, port_idx, val_idx, has_mask}
            
            for i, port_vals in enumerate(updated_returns):
                vals = port_vals if isinstance(port_vals, list) else [port_vals]
                for j, val in enumerate(vals):
                    is_img = isinstance(val, torch.Tensor) and val.ndim == 4
                    if is_img:
                        # Check if this image has a corresponding mask in the next port or value
                        has_mask = False
                        if i + 1 < len(updated_returns):
                             next_port_vals = updated_returns[i+1]
                             next_vals = next_port_vals if isinstance(next_port_vals, list) else [next_port_vals]
                             if len(next_vals) > 0 and isinstance(next_vals[0], torch.Tensor) and next_vals[0].ndim in (2, 3):
                                 has_mask = True
                        
                        port_structure.append({
                            "img_idx": current_img_idx,
                            "port_idx": i,
                            "val_idx": j, 
                            "has_mask": has_mask
                        })
                        current_img_idx += 1
            
            # Process updated images from frontend
            input_dir = folder_paths.get_input_directory()
            clipspace_dir = os.path.join(input_dir, "clipspace")

            for idx, img_info in enumerate(updated_images):
                if idx >= len(port_structure):
                    break
                # Check if this image points to a clipspace file (meaning it was edited)
                if img_info.get("subfolder") == "clipspace":
                    filename = img_info.get("filename")
                    full_path = os.path.join(clipspace_dir, filename)
                    
                    if os.path.exists(full_path):
                        try:
                            # Load edited image directly from the file specified by frontend
                            from PIL import Image, ImageOps
                            import node_helpers
                            
                            i = node_helpers.pillow(Image.open, full_path)
                            i = node_helpers.pillow(ImageOps.exif_transpose, i)
                            
                            if i.mode == 'I':
                                i = i.point(lambda i: i * (1 / 255))
                            
                            # Extract alpha channel as mask
                            mask = None
                            if 'A' in i.getbands():
                                mask = np.array(i.getchannel('A')).astype(np.float32) / 255.0
                                mask = 1. - torch.from_numpy(mask)
                            else:
                                # Fallback: No alpha, assume the image content itself is the mask (grayscale)
                                i_l = i.convert('L')
                                mask = np.array(i_l).astype(np.float32) / 255.0
                                mask = torch.from_numpy(mask)
                                # Usually grayscale masks are White=Masked(1), Black=Clear(0).
                                # But LoadImageMask inverts. We should check convention.

                            if mask is not None:
                                if mask.ndim == 2:
                                    mask = mask.unsqueeze(0) # [1, H, W]

                                # Apply mask to the correct port based on our mapping
                                struct = port_structure[idx]
                                port_idx = struct["port_idx"]
                                
                                if struct["has_mask"]:
                                    # Update existing mask in next port
                                    next_port_idx = port_idx + 1
                                    next_port_vals = updated_returns[next_port_idx]
                                    if isinstance(next_port_vals, list):
                                        updated_returns[next_port_idx] = [mask]
                                    else:
                                        updated_returns[next_port_idx] = mask
                                    mask_updated = True
                        except Exception as e:
                            print(f"[AnyPreviewPause] Error loading clipspace mask: {e}")
            
            if mask_updated:
                # Update result
                padding_count = MAX_FLOW_PORTS - len(updated_returns)
                if padding_count > 0:
                    result_tuple = tuple(updated_returns) + ([],) * padding_count
                else:
                    result_tuple = tuple(updated_returns[:MAX_FLOW_PORTS])
                
                result["result"] = result_tuple
                
                # Regenerate ALL preview images to reflect edited mask
                try:
                    # Flatten updated values
                    flat_updated_values = []
                    for port_vals in updated_returns:
                        if isinstance(port_vals, list):
                            flat_updated_values.extend(port_vals)
                        else:
                            flat_updated_values.append(port_vals)
                    
                    # Regenerate all RGBA images using updated mask data
                    all_editable_images = generate_editable_images(flat_updated_values)
                    
                    if all_editable_images:
                        # Save all preview images
                        all_saved_images, _ = save_images_for_preview(
                            self, all_editable_images, "CCNotes_mask"
                        )
                        result["ui"]["images"] = all_saved_images
                except Exception:
                    pass

        return result

class AutoMute:
    """
    AutoMute (Linked / Conditional) Node: Automatically mutes or unmutes target groups or nodes
    based on the presence and active state of specified nodes. It automatically mutes or unmutes
    target groups or nodes based on:
    - the presence of specified nodes (by name or name group)
    - and whether those nodes are currently active
    """
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "keys": ("STRING", {
                    "default": "",
                    "multiline": False,
                    # Node titles to monitor, separated by commas (e.g., get_mask1, get_mask2)
                }),
            }
        }
    
    RETURN_TYPES = ()
    FUNCTION = "noop"
    CATEGORY = "CCNotes/Utils"
    OUTPUT_NODE = True
    DESCRIPTION = "AutoMute 🎛: Automatically mutes/unmutes target groups/nodes based on monitored nodes. Works like workflow autopilot! 🚀"
    
    def noop(self, keys="", **kwargs):
        """No-op, all logic is implemented in frontend. No return value"""
        return ()

class ImageMaskComposite(SaveImage):
    def __init__(self):
        self.output_dir = folder_paths.get_temp_directory()
        self.type = "temp"
        self.prefix_append = "_temp_" + ''.join(random.choice("abcdefghijklmnopqrstupvxyz") for x in range(5))
        self.compress_level = 4

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "mask_opacity": ("FLOAT", {
                    "default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01
                }),
                "mask_color": ("STRING", {"default": "255, 255, 255"}),
            },
            "optional": {
                "image": ("IMAGE",),
                "mask": ("MASK",),
            },
            "hidden": {"unique_id": "UNIQUE_ID"},
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("composite",)
    FUNCTION = "process_composite"
    OUTPUT_NODE = True
    CATEGORY = "CCNotes/Image & Mask"

    @classmethod
    def IS_CHANGED(s, **kwargs):
        return 0

    def process_composite(self, mask_opacity=1.0, mask_color="255, 255, 255", image=None, mask=None, unique_id=None):
        from .utility.image_utility import parse_color
        unique_id_str = str(unique_id[0] if isinstance(unique_id, list) and unique_id else unique_id)

        if mask is None:
            result = image
        elif image is None:
            result = mask.reshape((-1, 1, mask.shape[-2], mask.shape[-1])).movedim(1, -1).expand(-1, -1, -1, 3)
        else:
            r, g, b, _ = parse_color(mask_color)
            result = composite_image_with_color(image, mask, (r, g, b), mask_opacity)
        
        all_saved_images, _ = save_images_for_preview(self, [result], "CCNotes_preview")
        frontend_data = {"images": all_saved_images} if all_saved_images else {}
        send_preview_event(unique_id_str, frontend_data, "preview")
        return {"ui": frontend_data, "result": (result,)}

class MakeAnyList:
    @classmethod
    def INPUT_TYPES(s):
        return {}

    RETURN_TYPES = (any_type,)
    OUTPUT_IS_LIST = (True,)
    FUNCTION = "doit"
    CATEGORY = "CCNotes/Utils"

    def doit(self, **kwargs):
        values = []
        for k, v in kwargs.items():
            if v is not None:
                values.append(v)
        return (values,)

class PrimitivePlus:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {},
            "hidden": {
                "prompt": "PROMPT",
                "unique_id": "UNIQUE_ID",
                "extra_pnginfo": "EXTRA_PNGINFO",
            }
        }

    RETURN_TYPES = (any_type,) * MAX_FLOW_PORTS
    RETURN_NAMES = tuple(f"connect_to_widget_input_{i}" for i in range(1, MAX_FLOW_PORTS + 1))
    FUNCTION = "proxy_widget"
    CATEGORY = "CCNotes/Utils"
    OUTPUT_IS_LIST = (True,) * MAX_FLOW_PORTS
    DESCRIPTION = "Manages and proxies multiple Primitive-style widgets from different nodes in a single control hub."

    def normalize_value(self, value):
        if isinstance(value, list) and len(value) >= 2 and isinstance(value[1], int):
            value = value[0]
        if value is None or value == "" or (isinstance(value, list) and len(value) == 0):
            return [""]
        elif not isinstance(value, list):
            return [value]
        else:
            return [item if item is not None else "" for item in value]

    def proxy_widget(self, prompt=None, unique_id=None, extra_pnginfo=None, **kwargs):
        try:
            input_values = {}
            for key, value in kwargs.items():
                if key.startswith("connect_to_widget_input"):
                    input_values[key] = self.normalize_value(value)
            
            # Fallback to prompt if inputs not in kwargs (though usually they are)
            if not input_values and prompt and unique_id:
                node_info = prompt.get(str(unique_id), {})
                if node_info and 'inputs' in node_info:
                    for key, value in node_info['inputs'].items():
                        if key.startswith("connect_to_widget_input"):
                            input_values[key] = self.normalize_value(value)
                            
            output_values = []
            for i in range(MAX_FLOW_PORTS):
                port_key = f"connect_to_widget_input_{i+1}"
                port_value = input_values.get(port_key, [""])
                output_values.append(port_value)
            return tuple(output_values)
        except Exception as e:
            return handle_error_safe(e, "PrimitivePlus failed", MAX_FLOW_PORTS)

class SwitchAny:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "switch": ("BOOLEAN", {"default": False}),
            },
            "optional": {
                "true": (any_type,),
                "false": (any_type,),
            }
        }

    RETURN_TYPES = (any_type,)
    RETURN_NAMES = ("output",)
    FUNCTION = "switch"
    CATEGORY = "CCNotes/Utils"

    def switch(self, switch, true=None, false=None):
        return (true if switch else false,)
        
class SwitchAnyMute:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "switch": ("BOOLEAN", {"default": False}),
            },
            "optional": {
                "true": (any_type, {"lazy": True}),
                "false": (any_type, {"lazy": True}),
            }
        }

    RETURN_TYPES = (any_type,)
    RETURN_NAMES = ("output",)
    FUNCTION = "switch"
    CATEGORY = "CCNotes/Utils"
    DESCRIPTION = "Automatic Switching (Efficiency Version) - Keep unselected input silent to optimize performance"

    def check_lazy_status(self, switch, true=None, false=None):
        needed = []
        if switch:
            if true is None:
                needed.append("true")
        else:
            if false is None:
                needed.append("false")
        return needed

    def switch(self, switch, true=None, false=None):
        return (true if switch else false,)

class SwitchAuto:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "optional": {
                "input1": (any_type,),
                "input2": (any_type,),
            }
        }

    RETURN_TYPES = (any_type,)
    RETURN_NAMES = ("output",)
    FUNCTION = "switch"
    CATEGORY = "CCNotes/Utils"

    def switch(self, input1=None, input2=None):
        if input1 is not None:
            return (input1,)
        elif input2 is not None:
            return (input2,)
        else:
            return (None,)

class SwitchOutput:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "input_data": (any_type, {"lazy": True}),
                "route": (["output1", "output2", "none"], {"default": "none"}),
            }
        }

    RETURN_TYPES = (any_type, any_type)
    RETURN_NAMES = ("output1", "output2")
    FUNCTION = "switch"
    CATEGORY = "CCNotes/Utils"

    def check_lazy_status(self, input_data=None, route="none"):
        needed = []
        if route == "output1":
            needed.append("input_data")
        elif route == "output2":
            needed.append("input_data")
        return needed

    def switch(self, input_data=None, route="none"):
        out1, out2 = None, None
        if route == "output1":
            out1 = input_data
        elif route == "output2":
            out2 = input_data
        return (out1, out2)
