with open('src/main.py', 'r') as f:
    content = f.read()

# Restructure: remove finally block, put cleanup after try/except
old = """        except KeyboardInterrupt:
            logger.warning("Interrupted while processing %s", video_id)
            raise
        except Exception as exc:
            logger.error("Unexpected error processing %s: %s", video_id, exc, exc_info=True)
            self.stats['errors'] += 1
            return False
        finally:
            # Clean up audio (regenerable). Section files are small and kept
            # for resume; they're in data/temp/sections/ and auto-managed.
            if audio_path:
                try:
                    os.remove(audio_path)
                except OSError:
                    pass
            pass  # End of if audio_path block
        pass  # End of process_video_for_shorts method"""

new = """        except KeyboardInterrupt:
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
        # Clean up audio on success
        if audio_path:
            try:
                os.remove(audio_path)
            except OSError:
                pass"""

if old in content:
    content = content.replace(old, new)
    with open('src/main.py', 'w') as f:
        f.write(content)
    print('Restructured - removed finally block')
else:
    print('Pattern not found')
    idx = content.find('        except KeyboardInterrupt:')
    if idx >= 0:
        print(repr(content[idx:idx+500]))