"""Host media selection must not accidentally forward a different USB device."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from distro_build import vm_media


class MediaTests(unittest.TestCase):
    def test_audio_selects_duplex_pulse_and_can_explicitly_disable(self):
        runtime = {'qemu': '/fixture/qemu', 'environment': {'PULSE_SERVER': 'unix:/fixture/pulse'}}
        with patch.object(vm_media.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'none pa')):
            args, report = vm_media.audio_options(runtime, 'auto')
            self.assertIn('pa,id=hostaudio,server=unix:/fixture/pulse', args)
            self.assertIn('hda-duplex,bus=sound.0,audiodev=hostaudio', args)
            self.assertTrue(report['playback_and_capture'])
            args, report = vm_media.audio_options(runtime, 'none')
            self.assertIn('none,id=hostaudio', args)
            self.assertFalse(report['playback_and_capture'])
        with patch.object(vm_media.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'none')):
            self.assertEqual(vm_media.audio_options(runtime, 'auto')[1]['backend'], 'none')
            with self.assertRaisesRegex(RuntimeError, 'lacks'):
                vm_media.audio_options(runtime, 'pulse')
        runtime['environment']['PULSE_SERVER'] = 'unix:/fixture/pulse,other=option'
        with self.assertRaisesRegex(RuntimeError, 'commas'):
            vm_media.audio_options(runtime, 'auto')

    def test_camera_requires_selected_accessible_video_interface(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            usb = root / 'usb'
            device = usb / '001/002'
            sysfs = root / 'sysfs'
            interface = sysfs / '1-1/1-1:1.0'
            interface.mkdir(parents=True)
            (interface.parent / 'busnum').write_text('1')
            (interface.parent / 'devnum').write_text('2')
            options = {'usb_root': usb, 'sysfs_root': sysfs}
            with self.assertRaisesRegex(RuntimeError, 'usbipd'):
                vm_media.camera_options('1:2', **options)
            device.parent.mkdir(parents=True)
            device.touch()
            (interface / 'bInterfaceClass').write_text('03')
            with self.assertRaisesRegex(RuntimeError, 'no USB video'):
                vm_media.camera_options('1:2', **options)
            (interface / 'bInterfaceClass').write_text('0e')
            args, report = vm_media.camera_options('1:2', **options)
            self.assertEqual(args, ['-device', 'usb-host,bus=xhci.0,hostbus=1,hostaddr=2'])
            self.assertEqual(report['device'], str(device))
            with patch.object(vm_media.os, 'access', return_value=False):
                with self.assertRaisesRegex(RuntimeError, 'read/write'):
                    vm_media.camera_options('1:2', **options)
            for invalid in ['0:2', '1:128', '256:1', '1:2,hostport=3', '1-2']:
                with self.subTest(invalid=invalid), self.assertRaises(RuntimeError):
                    vm_media.camera_options(invalid, **options)
