import unittest
from check_fungus import first_jpeg


class FrameCaptureTests(unittest.TestCase):
    def test_extracts_one_jpeg_across_chunks(self):
        chunks = [b'--frame\r\nContent-Type: image/jpeg\r\n\r\n\xff', b'\xd8abc\xff', b'\xd9--frame']
        self.assertEqual(first_jpeg(chunks), b'\xff\xd8abc\xff\xd9')

    def test_truncated_frame_rejected(self):
        with self.assertRaises(RuntimeError):
            first_jpeg([b'header\xff\xd8abc'])

    def test_oversized_stream_rejected(self):
        with self.assertRaises(RuntimeError):
            first_jpeg([b'x' * 20], limit=10)


if __name__ == '__main__':
    unittest.main()
