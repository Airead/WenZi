#!/usr/bin/env python3
"""Diagnostic script to debug microphone capture issues.

Usage: uv run python dev/debug_mic.py [--device NAME_SUBSTRING] [--seconds 3]

Tests:
1. Device enumeration — is the device visible?
2. Device ID resolution — can we get CoreAudio ID?
3. AVAudioEngine capture — does the tap produce audio frames?
4. Audio level analysis — is the signal silent or normal?
"""

import argparse
import queue
import struct
import sys
import time


def main():
    parser = argparse.ArgumentParser(description="Debug microphone capture")
    parser.add_argument("--device", "-d", default="EDIFIER", help="Device name substring to match")
    parser.add_argument("--seconds", "-s", type=float, default=3, help="Capture duration in seconds")
    args = parser.parse_args()

    # Lazy imports to avoid startup noise
    from AVFoundation import AVAudioEngine, AVCaptureDevice, AVMediaTypeAudio

    print("=" * 60)
    print("WenZi Microphone Diagnostic")
    print("=" * 60)

    # --- Step 1: Enumerate devices ---
    print("\n[1] Enumerating audio input devices...")
    devices = AVCaptureDevice.devicesWithMediaType_(AVMediaTypeAudio)
    target_device = None
    for d in devices:
        name = d.localizedName()
        uid = d.uniqueID()
        marker = ""
        if args.device.lower() in name.lower():
            target_device = d
            marker = "  <-- TARGET"
        print(f"  - {name} (uid={uid}){marker}")

    if not target_device:
        print(f"\n❌ No device matching '{args.device}' found!")
        print("   Check System Settings → Sound → Input to verify the device is listed.")
        sys.exit(1)

    target_uid = target_device.uniqueID()
    target_name = target_device.localizedName()
    print(f"\n✅ Found target: {target_name} (uid={target_uid})")

    # --- Step 2: Resolve CoreAudio device ID ---
    print("\n[2] Resolving CoreAudio AudioDeviceID...")
    sys.path.insert(0, "src")
    from wenzi.audio.recorder import _resolve_device_id

    dev_id = _resolve_device_id(target_uid)
    if dev_id is None:
        print(f"❌ Failed to resolve UID '{target_uid}' to a CoreAudio device ID!")
        print("   The device may be disconnected or not registered as an audio input in CoreAudio.")
        sys.exit(1)
    print(f"✅ Resolved to AudioDeviceID={dev_id}")

    # --- Step 3: Check device stream configuration ---
    print("\n[3] Checking device stream configuration...")
    import ctypes
    import ctypes.util

    _ca = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreAudio"))

    class _Addr(ctypes.Structure):
        _fields_ = [
            ("mSelector", ctypes.c_uint32),
            ("mScope", ctypes.c_uint32),
            ("mElement", ctypes.c_uint32),
        ]

    _ca.AudioObjectGetPropertyDataSize.restype = ctypes.c_int32
    _ca.AudioObjectGetPropertyDataSize.argtypes = [
        ctypes.c_uint32, ctypes.POINTER(_Addr),
        ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32),
    ]
    _ca.AudioObjectGetPropertyData.restype = ctypes.c_int32
    _ca.AudioObjectGetPropertyData.argtypes = [
        ctypes.c_uint32, ctypes.POINTER(_Addr),
        ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_void_p,
    ]

    # Check input stream configuration (kAudioDevicePropertyStreamConfiguration, kAudioDevicePropertyScopeInput)
    kStreamConfig = 0x73636667  # 'scfg'
    kInput = 0x696E7074        # 'inpt'

    addr = _Addr(kStreamConfig, kInput, 0)
    size = ctypes.c_uint32(0)
    err = _ca.AudioObjectGetPropertyDataSize(dev_id, ctypes.byref(addr), 0, None, ctypes.byref(size))
    if err != 0:
        print(f"❌ Cannot query stream config (error {err}). Device may not have input streams.")
    else:
        # AudioBufferList: mNumberBuffers (UInt32) + array of AudioBuffer
        buf = (ctypes.c_uint8 * size.value)()
        err = _ca.AudioObjectGetPropertyData(dev_id, ctypes.byref(addr), 0, None, ctypes.byref(size), buf)
        if err == 0:
            n_buffers = int.from_bytes(bytes(buf[0:4]), 'little')
            # Each AudioBuffer: mNumberChannels(4) + mDataByteSize(4) + mData(8)
            total_input_channels = 0
            for i in range(n_buffers):
                offset = 4 + i * 16  # 4 bytes header + 16 bytes per AudioBuffer (aligned)
                if offset + 4 <= size.value:
                    ch = int.from_bytes(bytes(buf[offset:offset+4]), 'little')
                    total_input_channels += ch
            print(f"  Input stream buffers: {n_buffers}, total input channels: {total_input_channels}")
            if total_input_channels == 0:
                print("⚠️  Device has 0 input channels! It may be output-only or not configured for input.")
                print("   This is likely the root cause — this device has no microphone input capability,")
                print("   or macOS doesn't recognize its input streams.")
        else:
            print(f"  Cannot read stream config data (error {err})")

    # --- Step 4: Try AVAudioEngine capture ---
    print(f"\n[4] Attempting AVAudioEngine capture for {args.seconds}s...")

    audio_queue = queue.Queue(maxsize=5000)
    frame_count = [0]
    rms_values = []

    engine = AVAudioEngine.alloc().init()
    input_node = engine.inputNode()

    # Set device
    try:
        au = input_node.AUAudioUnit()
        au.setDeviceID_error_(dev_id, None)
        print(f"  Set device to {target_name} (id={dev_id})")
    except Exception as e:
        print(f"⚠️  Failed to set device: {e}")
        print("  Falling back to system default")

    hw_fmt = input_node.outputFormatForBus_(0)
    hw_sr = hw_fmt.sampleRate()
    hw_ch = hw_fmt.channelCount()
    print(f"  Hardware format: {hw_sr:.0f} Hz, {hw_ch} channel(s)")

    if hw_sr == 0:
        print("❌ Hardware sample rate is 0! Device is not providing audio.")
        sys.exit(1)

    def tap_block(buf, when):
        try:
            n = buf.frameLength()
            if n == 0:
                return
            channel0 = buf.floatChannelData()[0]
            raw = bytes(channel0.as_buffer(n))
            floats = struct.unpack(f"<{n}f", raw)

            # Compute RMS
            sum_sq = sum(s * s for s in floats)
            rms = (sum_sq / n) ** 0.5 * 32768

            frame_count[0] += 1
            rms_values.append(rms)
            audio_queue.put_nowait(raw)
        except Exception as e:
            print(f"  Tap error: {e}")

    buf_size = int(hw_sr * 0.02)  # 20ms
    input_node.installTapOnBus_bufferSize_format_block_(0, buf_size, hw_fmt, tap_block)

    engine.prepare()
    ok, err = engine.startAndReturnError_(None)
    if not ok:
        print(f"❌ AVAudioEngine failed to start: {err}")
        sys.exit(1)

    print(f"  Engine started. Recording for {args.seconds}s... (speak into the mic!)")
    time.sleep(args.seconds)

    input_node.removeTapOnBus_(0)
    engine.stop()

    # --- Step 5: Analyze results ---
    print(f"\n[5] Analysis:")
    print(f"  Total tap callbacks: {frame_count[0]}")
    print(f"  RMS samples collected: {len(rms_values)}")

    if not rms_values:
        print("❌ No audio frames received at all!")
        print("   The AVAudioEngine tap never fired. Possible causes:")
        print("   - Device is not a real input device (output-only)")
        print("   - Audio session conflict")
        print("   - macOS Microphone permission not granted to Terminal")
        sys.exit(1)

    avg_rms = sum(rms_values) / len(rms_values)
    max_rms = max(rms_values)
    min_rms = min(rms_values)
    above_threshold = sum(1 for r in rms_values if r >= 20)

    print(f"  RMS — avg: {avg_rms:.1f}, min: {min_rms:.1f}, max: {max_rms:.1f}")
    print(f"  Frames above silence threshold (20): {above_threshold}/{len(rms_values)}")

    if max_rms < 1:
        print("\n❌ DIAGNOSIS: Perfect silence — all RMS values near 0.")
        print("   The device produces frames but they contain no audio signal.")
        print("   Likely causes:")
        print("   - The device is a soundbar OUTPUT being incorrectly listed as an input")
        print("   - The device's mic is hardware-muted")
        print("   - macOS is routing a null/virtual input stream for this device")
        print("\n   → Check System Settings → Sound → Input and verify this device")
        print("     actually shows input level when you speak into it.")
    elif avg_rms < 20:
        print("\n⚠️  DIAGNOSIS: Very low audio level (below silence threshold).")
        print("   The mic captures some signal but it's too quiet.")
        print("   Try: System Settings → Sound → Input → increase input volume for this device.")
    elif avg_rms < 100:
        print("\n⚠️  DIAGNOSIS: Low audio level. May trigger silence detection intermittently.")
        print("   The mic works but signal is weak. Increase input volume or speak closer.")
    else:
        print("\n✅ DIAGNOSIS: Audio capture looks normal!")
        print("   The microphone is producing audible signal.")
        print("   If WenZi still shows 'No Audio Captured', the issue may be in")
        print("   device switching timing or recording session lifecycle.")

    # Show RMS distribution
    print("\n  RMS distribution:")
    brackets = [(0, 1, "silence"), (1, 20, "near-silent"), (20, 100, "quiet"),
                (100, 500, "low"), (500, 2000, "normal"), (2000, 99999, "loud")]
    for lo, hi, label in brackets:
        count = sum(1 for r in rms_values if lo <= r < hi)
        bar = "█" * int(count / len(rms_values) * 40) if rms_values else ""
        print(f"    {label:>12} ({lo:>5}-{hi:>5}): {count:>4} {bar}")


if __name__ == "__main__":
    main()
