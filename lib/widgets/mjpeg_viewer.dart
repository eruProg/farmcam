import 'dart:async';
import 'dart:typed_data';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;

/// Displays a live MJPEG stream from the RPi5.
///
/// Connects to the given [streamUrl] and renders successive JPEG frames.
class MjpegViewer extends StatefulWidget {
  final String streamUrl;
  const MjpegViewer({super.key, required this.streamUrl});

  @override
  State<MjpegViewer> createState() => _MjpegViewerState();
}

class _MjpegViewerState extends State<MjpegViewer> {
  Uint8List? _currentFrame;
  bool _isConnected = false;
  bool _hasError = false;
  StreamSubscription? _subscription;

  @override
  void initState() {
    super.initState();
    _connect();
  }

  @override
  void dispose() {
    _subscription?.cancel();
    super.dispose();
  }

  void _connect() async {
    _subscription?.cancel();
    setState(() {
      _hasError = false;
      _isConnected = false;
    });

    try {
      final request = http.Request('GET', Uri.parse(widget.streamUrl));
      final client = http.Client();
      final response = await client.send(request).timeout(
        const Duration(seconds: 8),
      );

      if (response.statusCode != 200) {
        if (mounted) setState(() => _hasError = true);
        return;
      }

      if (mounted) setState(() => _isConnected = true);

      // Parse multipart MJPEG stream
      final buffer = BytesBuilder();
      bool inImage = false;

      _subscription = response.stream.listen(
        (chunk) {
          for (int i = 0; i < chunk.length; i++) {
            if (!inImage) {
              // Look for JPEG SOI marker (0xFF 0xD8)
              if (i < chunk.length - 1 &&
                  chunk[i] == 0xFF &&
                  chunk[i + 1] == 0xD8) {
                inImage = true;
                buffer.clear();
                buffer.addByte(chunk[i]);
              }
            } else {
              buffer.addByte(chunk[i]);
              // Safety valve: a real frame is a few tens of KB. If we blow
              // way past that without ever seeing an end-of-image marker
              // (a dropped byte, a mid-frame disconnect, a server hiccup),
              // stop accumulating garbage forever — drop it and wait for
              // the next SOI instead of growing unbounded and eventually
              // janking the whole app.
              if (buffer.length > 6 * 1024 * 1024) {
                inImage = false;
                buffer.clear();
                continue;
              }
              // Look for JPEG EOI marker (0xFF 0xD9)
              if (chunk[i] == 0xD9 && i > 0 && chunk[i - 1] == 0xFF) {
                final frame = buffer.toBytes();
                if (mounted && frame.length > 100) {
                  setState(() => _currentFrame = Uint8List.fromList(frame));
                }
                inImage = false;
                buffer.clear();
              }
            }
          }
        },
        onError: (_) {
          if (mounted) setState(() => _hasError = true);
          // Auto-reconnect after 3s
          Future.delayed(const Duration(seconds: 3), () {
            if (mounted) _connect();
          });
        },
        onDone: () {
          if (mounted) {
            setState(() => _isConnected = false);
            Future.delayed(const Duration(seconds: 3), () {
              if (mounted) _connect();
            });
          }
        },
      );
    } catch (_) {
      if (mounted) {
        setState(() => _hasError = true);
        Future.delayed(const Duration(seconds: 5), () {
          if (mounted) _connect();
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_hasError) {
      return Container(
        color: const Color(0xFF18181B),
        child: Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.videocam_off_outlined,
                  size: 36, color: Color(0xFF71717A)),
              const SizedBox(height: 8),
              const Text('Stream unavailable',
                  style: TextStyle(
                      color: Color(0xFFA1A1AA),
                      fontSize: 13,
                      fontWeight: FontWeight.w500)),
              const SizedBox(height: 12),
              GestureDetector(
                onTap: _connect,
                child: Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 14, vertical: 6),
                  decoration: BoxDecoration(
                    color: const Color(0xFF27272A),
                    borderRadius: BorderRadius.circular(6),
                  ),
                  child: const Text('Retry',
                      style: TextStyle(
                          color: Color(0xFFD4D4D8),
                          fontSize: 12,
                          fontWeight: FontWeight.w600)),
                ),
              ),
            ],
          ),
        ),
      );
    }

    if (_currentFrame == null) {
      return Container(
        color: const Color(0xFF18181B),
        child: const Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              SizedBox(
                  width: 24,
                  height: 24,
                  child: CircularProgressIndicator(
                      strokeWidth: 2, color: Color(0xFF16A34A))),
              SizedBox(height: 10),
              Text('Connecting to camera…',
                  style: TextStyle(
                      color: Color(0xFFA1A1AA),
                      fontSize: 12,
                      fontWeight: FontWeight.w500)),
            ],
          ),
        ),
      );
    }

    return Image.memory(
      _currentFrame!,
      fit: BoxFit.contain,
      gaplessPlayback: true,
      errorBuilder: (_, __, ___) => Container(
        color: const Color(0xFF18181B),
        child: const Center(
          child: Icon(Icons.broken_image_outlined,
              size: 32, color: Color(0xFF71717A)),
        ),
      ),
    );
  }
}
