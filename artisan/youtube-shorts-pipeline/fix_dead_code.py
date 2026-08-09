with open('src/main.py', 'r') as f:
    content = f.read()

# Remove the dead code after the try/except block
old = """            return True

        except KeyboardInterrupt:
            logger.warning("Interrupted while processing %s", video_id)
            raise
        except Exception as exc:
            logger.error("Unexpected error processing %s: %s", video_id, exc, exc_info=True)
            self.stats['errors'] += 1
            return False
        return True

    def _upload_clips"""

new = """            return True

        except KeyboardInterrupt:
            logger.warning("Interrupted while processing %s", video_id)
            raise
        except Exception as exc:
            logger.error("Unexpected error processing %s: %s", video_id, exc, exc_info=True)
            self.stats['errors'] += 1
            # Clean up audio on error
            if audio_path:
                try:
                    os.remove(audio_path)
                except OSError:
                    pass
            return False

    def _upload_clips"""

if old in content:
    content = content.replace(old, new)
    with open('src/main.py', 'w') as f:
        f.write(content)
    print('Removed dead code')
else:
    print('Pattern not found')
    idx = content.find('            return True\n\n        except KeyboardInterrupt:')
    if idx >= 0:
        print('Found at', idx)
        print(repr(content[idx:idx+500]))