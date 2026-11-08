from pathlib import Path

from PIL import Image, ImageColor
import serial.tools.list_ports
import streamlit as st
import streamlit.components.v1 as components

from matrix_app.worker import get_worker
from matrix_app.youtube import youtube_url

st.set_page_config(page_title="22×22 LED Matrix", page_icon="🎨", layout="wide")
worker = get_worker()
draw_canvas = components.declare_component("matrix_drawing", path=str(Path(__file__).parent / "drawing_component"))


def change_setting(key):
    # Send only the field the user changed, never a stale browser's whole form.
    worker.configure(**{key: st.session_state[key]})


def blackout():
    worker.configure(mode="Blackout")


def connect():
    worker.connect(st.session_state["serial_port"].strip(), st.session_state["serial_baud"])


def disconnect():
    worker.connect("", st.session_state["serial_baud"])


def play_youtube():
    try:
        worker.play_youtube(youtube_url(st.session_state["youtube_input"]))
    except ValueError as exc:
        st.session_state["control_error"] = str(exc)


def stop_youtube():
    worker.configure(youtube_url="")


def refresh_gallery():
    try:
        directory = st.session_state["gallery_input"].strip()
        recursive = st.session_state["gallery_recursive_input"]
        paths = []
        if directory:
            folder = Path(directory).expanduser()
            if not folder.is_dir():
                raise ValueError("Directory not found. Enter a path on the Pi, not on your phone or laptop.")
            entries = folder.rglob("*") if recursive else folder.iterdir()
            paths = sorted(str(p) for p in entries if p.is_file() and p.suffix.lower() in
                           {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"})
        worker.configure(gallery_directory=directory, gallery_recursive=recursive, gallery=tuple(paths))
    except (OSError, ValueError) as exc:
        st.session_state["control_error"] = str(exc)


# Callbacks execute before this point. Refresh widget values from authoritative
# state on every full run. Merely opening/rerunning a page never writes settings.
state = worker.control_state()
for key in ("mode", "brightness", "fps", "axis", "serpentine", "rotation", "flip_x", "flip_y",
            "color_order", "clock_color", "use_24h", "blink", "fit", "interval", "youtube_repeat"):
    st.session_state[key] = state[key]
st.session_state["seen_revision"] = state["revision"]

# These are local drafts: another user's brightness change should not erase a
# partly typed link/path/port. Sync only when the corresponding shared value changes.
for key, value in (("youtube_input", state["youtube_url"]),
                   ("gallery_input", state["gallery_directory"]),
                   ("gallery_recursive_input", state["gallery_recursive"]),
                   ("serial_port", state["port"]), ("serial_baud", state["baud"])):
    marker = "shared_" + key
    if key not in st.session_state or st.session_state.get(marker) != value:
        st.session_state[key] = value
        st.session_state[marker] = value

st.title("22×22 LED Matrix")
st.caption("One shared panel · anyone on this trusted LAN can control it · playback continues with all browsers closed")
if error := st.session_state.pop("control_error", ""):
    st.error(error)

with st.sidebar:
    st.header("Connection")
    ports = [port.device for port in serial.tools.list_ports.comports()]
    st.caption("Detected: " + (", ".join(ports) or "no serial devices"))
    st.text_input("Serial port", key="serial_port", placeholder="/dev/serial/by-id/…")
    st.selectbox("Baud rate (must match firmware)", [250000, 500000, 115200], key="serial_baud")
    connect_col, disconnect_col = st.columns(2)
    connect_col.button("Connect", on_click=connect, use_container_width=True)
    disconnect_col.button("Disconnect", on_click=disconnect, use_container_width=True)
    st.slider("Brightness (%)", 0, 100, key="brightness", on_change=change_setting, args=("brightness",))
    st.caption("Start low. This is not a calibrated power/current limiter.")
    st.slider("Target frames/sec", 1, 20, key="fps", on_change=change_setting, args=("fps",))
    st.button("⬛ Blackout", on_click=blackout, use_container_width=True)
    with st.expander("Panel wiring / orientation"):
        st.selectbox("Wiring runs along", ["Rows", "Columns"], key="axis", on_change=change_setting, args=("axis",))
        st.checkbox("Serpentine (alternating direction)", key="serpentine", on_change=change_setting, args=("serpentine",))
        st.selectbox("Rotation clockwise", [0, 90, 180, 270], key="rotation", on_change=change_setting, args=("rotation",))
        st.checkbox("Flip left/right", key="flip_x", on_change=change_setting, args=("flip_x",))
        st.checkbox("Flip up/down", key="flip_y", on_change=change_setting, args=("flip_y",))
        st.selectbox("Channel correction", ["RGB", "RBG", "GRB", "GBR", "BRG", "BGR"], key="color_order",
                     on_change=change_setting, args=("color_order",))
        st.caption("Normally RGB here; firmware already uses WS2812B GRB signaling.")

mode = st.selectbox("Mode", ["Blackout", "Clock", "YouTube", "Gallery", "Drawing", "Wiring test"],
                    key="mode", on_change=change_setting, args=("mode",))
controls, preview = st.columns([2, 1])

with controls:
    if mode == "Clock":
        st.color_picker("Clock color", key="clock_color", on_change=change_setting, args=("clock_color",))
        st.toggle("24-hour clock", key="use_24h", on_change=change_setting, args=("use_24h",))
        st.toggle("Blink colon", key="blink", on_change=change_setting, args=("blink",))
        st.caption("Uses the Pi's local timezone. A bottom-right dot indicates PM in 12-hour mode.")
    elif mode == "YouTube":
        st.text_input("YouTube video or playlist URL", key="youtube_input", placeholder="https://www.youtube.com/playlist?list=…")
        st.checkbox("Repeat video / entire playlist", key="youtube_repeat", on_change=change_setting, args=("youtube_repeat",))
        st.selectbox("Image fitting", ["Crop", "Fit"], key="fit", on_change=change_setting, args=("fit",))
        play, stop = st.columns(2)
        play.button("Play / restart", on_click=play_youtube, use_container_width=True)
        stop.button("Stop video", on_click=stop_youtube, use_container_width=True)
        st.caption("Streams on the host without saving video files. No audio. Unavailable entries are skipped. Closing browsers does not stop playback. Use only videos you are authorized to stream.")
    elif mode == "Gallery":
        with st.form("gallery_directory"):
            st.text_input("Image directory on the Pi", key="gallery_input", placeholder="/home/youruser/Pictures")
            st.checkbox("Include subdirectories", key="gallery_recursive_input")
            st.form_submit_button("Load / refresh directory", on_click=refresh_gallery)
        st.slider("Seconds per image", 0.5, 60.0, step=0.5, key="interval", on_change=change_setting, args=("interval",))
        st.selectbox("Image fitting", ["Crop", "Fit"], key="fit", on_change=change_setting, args=("fit",))
        st.caption(f"{len(state['gallery'])} images loaded. GIFs use their first frame.")
        if state["gallery_directory"] and not state["gallery"]:
            st.warning("No supported images in this directory.")
    elif mode == "Drawing":
        color = st.color_picker("Brush color", "#ff4000")
        erase = st.checkbox("Eraser")
        st.button("Clear drawing", on_click=worker.clear_drawing)
        result = draw_canvas(color="#000000" if erase else color, pixels=state["draw_pixels"],
                             revision=state["drawing_revision"], key="drawing", default=None)
        if isinstance(result, dict) and result.get("revision") == state["drawing_revision"]:
            pixels = result.get("pixels", [])
            if isinstance(pixels, list) and len(pixels) == 484:
                try:
                    image = Image.new("RGB", (22, 22))
                    image.putdata([ImageColor.getrgb(value) for value in pixels])
                    if worker.set_drawing(image, state["drawing_revision"]):
                        st.rerun()
                except (ValueError, TypeError, AttributeError):
                    st.error("Invalid drawing data")
        st.caption("Shared canvas. Stale strokes are ignored if another user has updated the drawing. Drawings remain until cleared or the app restarts.")
    elif mode == "Wiring test":
        st.markdown("Expected corners: **red** top-left, **green** top-right, **blue** bottom-left, **white** bottom-right. White guides run along the top and left edges. The middle red/green rows should both brighten toward the right.")
        st.caption("Adjust wiring and orientation in the sidebar until the panel matches the preview.")
    else:
        st.info("Panel off. Select a mode to start; the preview works without hardware.")


@st.fragment(run_every=0.5)
def live_preview():
    current = worker.control_state()
    if current["revision"] != st.session_state["seen_revision"]:
        st.rerun()
    image, status, error, actual_fps = worker.snapshot()
    st.subheader("Preview")
    st.image(image.resize((330, 330), Image.Resampling.NEAREST), caption="Logical image before wiring transforms and brightness")
    st.caption(status)
    st.caption(f"Current mode: {current['mode']}")
    if current["mode"] == "YouTube":
        st.caption(worker.youtube.snapshot()[1])
    st.caption(f"Output loop: {actual_fps:.1f} fps")
    if error:
        st.warning(error)


with preview:
    live_preview()
