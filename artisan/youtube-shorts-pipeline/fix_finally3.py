with open('src/main.py', 'r') as f:
    content = f.read()

# Replace the try/except/finally with try/except (cleanup in except and before return)
old = "            return True\n        except KeyboardInterrupt:\n            logger.warning(\"Interrupted while processing %s\", video_id)\n            raise\n        except Exception as exc:\n            logger.error(\"Unexpected error processing %s: %s\", video_id, exc, exc_info=True)\n            self.stats['errors'] += 1\n            return False\n        finally:\n            # Clean up audio regardless of success or exception\n            if audio_path:\n                try:\n                    os.remove(audio_path)\n                except OSError:\n                    pass\n\n    def _upload_"

new = "            # Clean up audio on success\n            if audio_path:\n                try:\n                    os.remove(audio_path)\n                except OSError:\n                    pass\n            return True\n        except KeyboardInterrupt:\n            logger.warning(\"Interrupted while processing %s\", video_id)\n            # Clean up audio on interrupt\n            if audio_path:\n                try:\n                    os.remove(audio_path)\n                except OSError:\n                    pass\n            raise\n        except Exception as exc:\n            logger.error(\"Unexpected error processing %s: %s\", video_id, exc, exc_info=True)\n            self.stats['errors'] += 1\n            # Clean up audio on error\n            if audio_path:\n                try:\n                    os.remove(audio_path)\n                except OSError:\n                    pass\n            return False\n\n    def _upload_"

if old in content:
    content = content.replace(old, new)
    with open('src/main.py', 'w') as f:
        f.write(content)
    print('Restructured - removed finally block')
else:
    print('Pattern not found')