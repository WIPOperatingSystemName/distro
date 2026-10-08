from pathlib import Path
import runpy
import hashlib
import json
import tempfile
import unittest

check = runpy.run_path(str(Path(__file__).resolve().parents[1] / "tools/check-wayland-window.py"))["check"]
check_vm = runpy.run_path(str(Path(__file__).resolve().parents[1] / "tools/check-wayland-window.py"))["check_vm"]

class WaylandWindowLogTests(unittest.TestCase):
    def test_display_sync_callback_does_not_qualify_window(self):
        log = ''' -> xdg_wm_base@1.get_xdg_surface(new id xdg_surface@2, wl_surface@3)
 -> xdg_surface@2.get_toplevel(new id xdg_toplevel@4)
 -> xdg_toplevel@4.set_app_id("org.example.Test")
 xdg_surface@2.configure(12)
 -> xdg_surface@2.ack_configure(12)
 -> wl_shm_pool@5.create_buffer(new id wl_buffer@6, 0, 480, 320, 1920, 1)
 -> wl_surface@3.attach(wl_buffer@6, 0, 0)
 -> wl_surface@3.commit()
 -> wl_display@1.sync(new id wl_callback@7)
 wl_callback@7.done(42)
'''
        self.assertFalse(check(log, "org.example.Test")["passed"])
        self.assertTrue(check(log + ''' -> wl_surface@3.frame(new id wl_callback@8)
 -> wl_surface@3.commit()
 wl_callback@8.done(43)
''', "org.example.Test")["passed"])

    def test_rust_event_format_and_wrong_surface(self):
        log = '''[rs] -> xdg_wm_base@1.get_xdg_surface(xdg_surface@2, wl_surface@3)
[rs] -> xdg_surface@2.get_toplevel(xdg_toplevel@4)
[rs] -> xdg_toplevel@4.set_app_id(Some("org.example.Test"))
[rs] <- xdg_surface@2.configure, (12)
[rs] -> xdg_surface@2.ack_configure(12)
[rs] -> wl_shm_pool@5.create_buffer(wl_buffer@6, 0, 480, 320, 1920, 1)
[rs] -> wl_surface@3.attach(wl_buffer@6, 0, 0)
[rs] -> wl_surface@3.commit()
[rs] -> wl_surface@9.frame(wl_callback@8)
[rs] -> wl_surface@3.commit()
[rs] <- wl_callback@8.done, (43)
'''
        self.assertFalse(check(log, "org.example.Test")["passed"])
        self.assertTrue(check(log.replace("wl_surface@9.frame", "wl_surface@3.frame"), "org.example.Test")["passed"])
        self.assertFalse(check(log, "org.example.Other")["passed"])

    def test_wayland_124_title_and_consumed_shm_without_frame(self):
        # Preserve the actual libwayland 1.24 notation and sequence observed
        # in the target Telorgon File Explorer guest log.
        log = '''[3776540.789] -> xdg_wm_base#13.get_xdg_surface(new id xdg_surface#29, wl_surface#28)
[3776540.792] -> xdg_surface#29.get_toplevel(new id xdg_toplevel#30)
[3776540.824] -> xdg_toplevel#30.set_title("Telorgon Files")
[3776540.829] -> wl_surface#28.commit()
[3776540.833] -> wl_display#1.sync(new id wl_callback#35)
[3776544.084] xdg_surface#29.configure(1)
[3776544.085] wl_callback#35.done(1)
[3776544.097] -> xdg_surface#29.ack_configure(1)
[3776775.679] -> wl_shm_pool#37.create_buffer(new id wl_buffer#38, 0, 1240, 800, 4960, 1)
[3776778.722] -> wl_surface#28.attach(wl_buffer#38, 0, 0)
[3776778.729] -> wl_surface#28.commit()
[3777565.489] wl_surface#28.enter(wl_output#5)
[3778160.184] wl_buffer#38.release()
'''
        report = check(log, "org.telorgon.FileExplorer", title="Telorgon Files", evidence="mapped-shm")
        self.assertTrue(report["passed"])
        self.assertEqual(report["windows"][0]["identity_method"], "set_title")
        self.assertIsNone(report["windows"][0]["app_id_observed"])
        self.assertFalse(report["windows"][0]["surface_frame_callback_proved"])
        self.assertFalse(check(log, "org.telorgon.FileExplorer")["passed"])
        self.assertFalse(check(log.replace("wl_buffer#38.release()", "wl_buffer#39.release()"),
                               "org.telorgon.FileExplorer", title="Telorgon Files", evidence="mapped-shm")["passed"])
        self.assertFalse(check(log.replace("wl_surface#28.enter", "wl_surface#40.enter"),
                               "org.telorgon.FileExplorer", title="Telorgon Files", evidence="mapped-shm")["passed"])
        conflicting = log.replace('set_title("Telorgon Files")', 'set_title("Telorgon Files")\n -> xdg_toplevel#30.set_app_id("org.example.Other")')
        self.assertFalse(check(conflicting, "org.telorgon.FileExplorer", title="Telorgon Files", evidence="mapped-shm")["passed"])
        # Reusing a numeric ID cannot turn a different buffer's release into
        # evidence that the original surface attachment was consumed.
        reused = log.replace('wl_buffer#38.release()', '-> wl_shm_pool#37.create_buffer(new id wl_buffer#38, 0, 10, 10, 40, 1)\n wl_buffer#38.release()')
        self.assertFalse(check(reused, "org.telorgon.FileExplorer", title="Telorgon Files", evidence="mapped-shm")["passed"])

    def test_sharp_object_frame_not_display_sync(self):
        log = ''' -> xdg_wm_base#1.get_xdg_surface(new id xdg_surface#2, wl_surface#3)
 -> xdg_surface#2.get_toplevel(new id xdg_toplevel#4)
 -> xdg_toplevel#4.set_app_id("org.example.Test")
 xdg_surface#2.configure(12)
 -> xdg_surface#2.ack_configure(12)
 -> wl_shm_pool#5.create_buffer(new id wl_buffer#6, 0, 480, 320, 1920, 1)
 -> wl_surface#3.attach(wl_buffer#6, 0, 0)
 -> wl_surface#3.frame(new id wl_callback#8)
 -> wl_surface#3.commit()
 wl_callback#8.done(43)
'''
        self.assertTrue(check(log, "org.example.Test")["passed"])
        self.assertFalse(check(log.replace("wl_surface#3.frame", "wl_display#1.sync"), "org.example.Test")["passed"])
        premature = log.replace(' -> wl_surface#3.commit()\n wl_callback#8.done(43)', 'wl_callback#8.done(43)\n -> wl_display#1.sync(new id wl_callback#8)\n -> wl_surface#3.commit()\n wl_callback#8.done(44)')
        self.assertFalse(check(premature, "org.example.Test")["passed"])

    def test_changed_image_and_incomplete_blocks_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "disk.img"
            image.write_bytes(b"original fixture image")
            serial = root / "serial.log"
            serial.write_text("CUSTOM_WAYLAND_LOG_BEGIN name=files\n")
            receipt = root / "result.json"
            receipt.write_text(json.dumps({"image": str(image), "image_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
                "serial_log": str(serial), "success": True, "desktop_input_sent": True}))
            with self.assertRaisesRegex(ValueError, "Completed"):
                check_vm(receipt)
            image.write_bytes(b"different image")
            with self.assertRaisesRegex(ValueError, "differs"):
                check_vm(receipt)

    def test_vm_app_buffers_and_separate_presentation_probe(self):
        def app_log(title):
            return ''' -> xdg_wm_base#13.get_xdg_surface(new id xdg_surface#29, wl_surface#28)
 -> xdg_surface#29.get_toplevel(new id xdg_toplevel#30)
 -> xdg_toplevel#30.set_title("''' + title + '''")
 xdg_surface#29.configure(1)
 -> xdg_surface#29.ack_configure(1)
 -> wl_shm_pool#37.create_buffer(new id wl_buffer#38, 0, 1240, 800, 4960, 1)
 -> wl_surface#28.attach(wl_buffer#38, 0, 0)
 -> wl_surface#28.commit()
 wl_surface#28.enter(wl_output#5)
 wl_buffer#38.release()
'''
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "disk.img"
            image.write_bytes(b"parser binding fixture image")
            serial = root / "serial.log"
            marker = "CUSTOM_DESKTOP_WINDOW_INPUT_OK configured=1 initial_presented=1 keyboard_focus=1 key_pressed=1 redraw_presented=1\n"
            content = marker
            for name, title in (("files", "Telorgon Files"), ("settings", "Telorgon Settings")):
                content += "CUSTOM_WAYLAND_LOG_BEGIN name=" + name + "\n" + app_log(title) + "CUSTOM_WAYLAND_LOG_END name=" + name + "\n"
            serial.write_text(content)
            receipt = root / "result.json"
            receipt.write_text(json.dumps({"image": str(image), "image_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
                "serial_log": str(serial), "success": True, "desktop_input_sent": True}))
            report = check_vm(receipt)
            self.assertTrue(report["passed"])
            self.assertTrue(report["input_presentation_proved"])
            self.assertTrue(all(not app["windows"][0]["surface_frame_callback_proved"] for app in report["apps"]))
            serial.write_text(content.replace("wl_buffer#38.release()", "wl_buffer#40.release()", 1))
            self.assertFalse(check_vm(receipt)["passed"])
            serial.write_text(content.replace(marker, ""))
            self.assertFalse(check_vm(receipt)["passed"])

if __name__ == "__main__":
    unittest.main()
