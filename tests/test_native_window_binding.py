import dataclasses
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from nlc import native_window_binding as binding


class Backend:
    def __init__(self):
        self.launch_value = 123.5
        self.nodes = {
            'button': dict(pid=4, AXRole='AXButton', AXParent='window', frame=(20, 30, 40, 20)),
            'child': dict(pid=4, AXRole='AXStaticText', AXParent='button', frame=(25, 35, 10, 10)),
            'other': dict(pid=4, AXRole='AXButton', AXParent='window', frame=(20, 30, 40, 20)),
            'window': dict(pid=4, AXRole='AXWindow', frame=(10, 20, 300, 200)),
        }
        self.cg = [dict(id=7, pid=4, onscreen=True, layer=0, bounds=(10, 20, 300, 200))]
        self.hit_value = 'button'
        self.number = 7

    def equal(self, a, b): return a == b
    def launch(self, pid): return self.launch_value
    def pid(self, node): return self.nodes[node]['pid']
    def attr(self, node, name): return self.nodes[node].get(name)
    def frame(self, node): return self.nodes[node]['frame']
    def window_id(self, node): return self.number
    def windows(self): return self.cg
    def hit(self, pid, point): return self.hit_value


class NativeWindowBindingTests(unittest.TestCase):
    def test_opaque_window_point_needs_no_hit_test_and_is_revalidated(self):
        backend=Backend()
        backend.nodes['window']['AXSubrole']='AXStandardWindow'
        backend.require_ready=lambda *a:None
        backend.hit=lambda *a:self.fail('opaque point must not resolve a fictitious AX control')
        with patch.object(binding, '_load_backend', return_value=backend):
            point=binding.capture_binding('window',4,point_kind='window_point',relative_point=(80,90))
        self.assertEqual(point.point,(90,110))
        self.assertEqual(point.relative_point,(80,90))
        self.assertIs(binding.validate_binding(point),point)
        backend.nodes['window']['frame']=(11,20,300,200)
        backend.cg[0]['bounds']=(11,20,300,200)
        with self.assertRaises(binding.NativeWindowBindingError):binding.validate_binding(point)

    def test_bad_coordinates_fail_before_native_imports_and_outside_points_fail(self):
        with patch.object(binding,'_load_backend') as load:
            for point in (None,(True,0),(-1,2),(float('nan'),2),(1,10**1000),(1,2,3),'1,2'):
                with self.subTest(point=point),self.assertRaises(binding.NativeWindowBindingError):
                    binding.capture_binding('window',4,point_kind='window_point',relative_point=point)
            load.assert_not_called()
        backend=Backend();backend.nodes['window']['AXSubrole']='AXStandardWindow'
        backend.require_ready=lambda *a:None
        for point in ((300,0),(0,200),(301,201)):
            with patch.object(binding,'_load_backend',return_value=backend),self.assertRaises(binding.NativeWindowBindingError):
                binding.capture_binding('window',4,point_kind='window_point',relative_point=point)

    def test_os_number_proxies_are_normalized_before_strict_geometry_checks(self):
        class Number:
            def __init__(self, value): self.value=value
            def __int__(self): return int(self.value)
            def __float__(self): return float(self.value)
        backend=binding._MacBackend.__new__(binding._MacBackend)
        backend.q=SimpleNamespace(kCGWindowListOptionAll=0, kCGWindowNumber='id',
            kCGWindowOwnerPID='pid',kCGWindowIsOnscreen='onscreen',
            kCGWindowLayer='layer',kCGWindowBounds='bounds',
            CGWindowListCopyWindowInfo=lambda *_:[{'id':Number(7),'pid':Number(4),
                'onscreen':True,'layer':0,'bounds':dict(zip(('X','Y','Width','Height'),
                                                        map(Number,(10,20,300,200))))}])
        row=backend.windows()[0]
        self.assertEqual(7,row['id']);self.assertEqual(4,row['pid'])
        self.assertEqual((10.,20.,300.,200.),binding._rect(row['bounds']))

    def capture(self, backend):
        with patch.object(binding, '_load_backend', return_value=backend):
            return binding.capture_binding('button', 4)

    def test_exact_and_descendant_hit_and_immutable_binding(self):
        for hit in ('button', 'child'):
            b = Backend(); b.hit_value = hit
            result = self.capture(b)
            self.assertEqual(result.point, (40, 40))
            self.assertIs(binding.validate_binding(result), result)
            with self.assertRaises(dataclasses.FrozenInstanceError):
                result.pid = 5

    def test_invalid_input_rejected_before_mac_imports(self):
        with patch.object(binding, '_load_backend') as load:
            for element, pid, launch in [(None,4,None),('x',True,None),('x',0,None),
                                        ('x',4,True),('x',4,float('nan')),('x',4,10**1000)]:
                with self.subTest(pid=pid,launch=launch), self.assertRaises(binding.NativeWindowBindingError):
                    binding.capture_binding(element,pid,launch)
            load.assert_not_called()

    def test_geometry_process_and_visibility_mutations_rejected(self):
        def moved(b):
            b.nodes['window']['frame'] = (11,20,300,200)
            b.cg[0]['bounds'] = (11,20,300,200)
        changes = [moved,
            lambda b: setattr(b,'launch_value',124),
            lambda b: b.cg[0].update(onscreen=False),
            lambda b: b.cg[0].update(pid=9),
            lambda b: b.cg[0].update(layer=1),
            lambda b: b.nodes['button'].update(pid=9),
            lambda b: b.nodes['button'].update(frame=(21,30,40,20)),
            lambda b: setattr(b,'hit_value','other'),
            lambda b: setattr(b,'hit_value','window'),
            lambda b: setattr(b,'number',8)]
        for change in changes:
            b=Backend(); result=self.capture(b); change(b)
            with self.subTest(change=change), self.assertRaises(binding.NativeWindowBindingError):
                binding.validate_binding(result)

    def test_window_identity_replacement_even_same_geometry_rejected(self):
        b=Backend(); result=self.capture(b)
        b.nodes['replacement']=dict(b.nodes['window'])
        b.nodes['button']['AXParent']='replacement'
        with self.assertRaises(binding.NativeWindowBindingError):
            binding.validate_binding(result)

    def test_capture_rejects_ambiguous_malformed_and_unbounded_ancestry(self):
        def cycle(b): b.nodes['button']['AXParent']='button'
        changes=[cycle, lambda b: b.cg.append(dict(b.cg[0])),
                 lambda b: b.nodes['button'].update(frame=(0,0,400,400)),
                 lambda b: b.nodes['button'].update(frame=(20,30,float('inf'),20)),
                 lambda b: b.nodes['window'].update(pid=9),
                 lambda b: b.nodes['child'].update(pid=9)]
        for change in changes:
            b=Backend(); b.hit_value='child'; change(b)
            with self.subTest(change=change), self.assertRaises(binding.NativeWindowBindingError):
                self.capture(b)
        b=Backend()
        for i in range(22):
            b.nodes[str(i)] = dict(pid=4, AXRole='AXGroup', AXParent=str(i+1))
        b.nodes['button']['AXParent']='0'
        b.nodes['21']['AXParent']='window'
        with self.assertRaises(binding.NativeWindowBindingError): self.capture(b)

    def test_requested_launch_and_unavailable_spi_fail_closed(self):
        with patch.object(binding,'_load_backend',return_value=Backend()):
            with self.assertRaises(binding.NativeWindowBindingError):
                binding.capture_binding('button',4,124)
        with patch.object(binding,'_load_backend',side_effect=binding.NativeWindowBindingUnavailable('SPI')):
            with self.assertRaises(binding.NativeWindowBindingUnavailable):
                binding.capture_binding('button',4)


class NativeTitlebarBindingTests(unittest.TestCase):
    def backend(self):
        b = Backend()
        b.nodes['window'].update(AXSubrole='AXStandardWindow', AXCloseButton='close')
        b.nodes['close'] = dict(pid=4, AXRole='AXButton', AXSubrole='AXCloseButton',
                                AXParent='window', frame=(18, 26, 14, 14))
        b.hit_value = 'window'
        b.require_ready = lambda window, pid: True
        return b

    def capture(self, b):
        with patch.object(binding, '_load_backend', return_value=b):
            return binding.capture_binding('window', 4, point_kind='titlebar')

    def test_readiness_refusal_prevents_titlebar_binding(self):
        b = self.backend()
        b.require_ready = unittest.mock.Mock(side_effect=binding.NativeWindowBindingError('modal'))
        with self.assertRaises(binding.NativeWindowBindingError):
            self.capture(b)

    def test_native_anchor_and_blank_window_hit(self):
        b = self.backend(); result = self.capture(b)
        self.assertEqual(result.point, (160, 33))
        self.assertEqual(result.point_kind, 'titlebar')
        self.assertIs(binding.validate_binding(result), result)
        b.hit_value = 'button'
        with self.assertRaises(binding.NativeWindowBindingError):
            binding.validate_binding(result)

    def test_child_controls_and_wrong_window_anchors_are_not_blank_titlebar(self):
        for change in (lambda b: setattr(b,'hit_value','child'),
                       lambda b: b.nodes['close'].update(pid=9),
                       lambda b: b.nodes['close'].update(frame=(18,100,14,14)),
                       lambda b: b.nodes['window'].update(AXSubrole='AXDialog')):
            b=self.backend();change(b)
            with self.subTest(change=change), self.assertRaises(binding.NativeWindowBindingError):
                self.capture(b)

    def test_covered_center_uses_another_verified_blank_point(self):
        b=self.backend()
        b.hit=lambda pid,point: 'button' if point[0]==160 else 'window'
        result=self.capture(b)
        self.assertEqual(result.point,(235,33))
        b.hit=lambda pid,point:'window'
        with self.assertRaises(binding.NativeWindowBindingError):
            binding.validate_binding(result)


if __name__ == '__main__': unittest.main()
