"""SmoothedCameraman — "heavy tripod" camera smoothing.

Vendored from openshorts (MIT, github.com/mutonby/openshorts, main.py),
which forked it from kamilstanuch/Autocrop-vertical. Unchanged logic;
extracted verbatim so the reframe engine can import it without dragging
openshorts' application code along.

Only moves when the subject leaves the centre safe zone; moves slowly and
linearly; a big target jump must repeat N frames before the camera follows
(detector errors are lone spikes; real moves persist).
"""
import os

ASPECT_RATIO = 9 / 16
JUMP_CONFIRM_FRAMES = max(int(os.environ.get("JUMP_CONFIRM_FRAMES", "3")), 1)


class SmoothedCameraman:
    """
    Handles smooth camera movement.
    Simplified Logic: "Heavy Tripod"
    Only moves if the subject leaves the center safe zone.
    Moves slowly and linearly.
    """
    def __init__(self, output_width, output_height, video_width, video_height,
                 aspect_ratio=ASPECT_RATIO):
        self.output_width = output_width
        self.output_height = output_height
        self.video_width = video_width
        self.video_height = video_height
        self.aspect_ratio = aspect_ratio

        # Initial State
        self.current_center_x = video_width / 2
        self.target_center_x = video_width / 2

        # Calculate crop dimensions once
        self.crop_height = video_height
        self.crop_width = int(self.crop_height * aspect_ratio)
        if self.crop_width > video_width:
            self.crop_width = video_width
            self.crop_height = int(self.crop_width / aspect_ratio)

        # Safe Zone: 20% of the video width
        # As long as the target is within this zone relative to current center,
        # DO NOT MOVE.
        self.safe_zone_radius = self.crop_width * 0.25

        # A target that teleports further than the safe zone in one detection
        # is far more often a detector error — a second face, a false positive,
        # a box snapping to a different body part — than a person who actually
        # moved that far. Committing to it immediately is what made the camera
        # swing: measured on real user footage, 22% of target updates jumped
        # more than the entire safe zone. So a big move has to REPEAT this many
        # times before the camera follows it; a wrong reading disappears on the
        # next detection and never moves the frame.
        #
        # The cost is latency on a genuinely fast move: at DETECT_STRIDE=4 and
        # 30fps, three confirmations is ~0.4s. That reads as an operator being
        # unhurried, which is the look we want, and it is far cheaper than the
        # whip-panning it replaces.
        #
        # Measured over 262s of TRACK footage from two real user videos
        # (26-jul-2026), confirm=1 -> 3: in-scene reversals 0.41/s -> 0.13/s
        # (-69%), camera travel 91px/s -> 60px/s (-34%). Per scene, 54 of 84 get
        # calmer and 23 are unchanged — but 7 get BUSIER, up to 59 -> 108px/s,
        # because committing later can leave the camera further to travel. Net
        # strongly positive, not universally so.
        self.jump_confirm_frames = JUMP_CONFIRM_FRAMES
        self._pending_target = None
        self._pending_count = 0

    def update_target(self, face_box):
        """Update the target centre from a detection, ignoring lone big jumps."""
        if not face_box:
            return
        x, y, w, h = face_box
        new_center = x + w / 2

        if abs(new_center - self.target_center_x) > self.safe_zone_radius:
            # Same big move as last time? Count it. Otherwise start counting
            # afresh — two contradictory outliers must not confirm each other.
            if (self._pending_target is not None
                    and abs(new_center - self._pending_target) <= self.safe_zone_radius):
                self._pending_count += 1
            else:
                self._pending_target = new_center
                self._pending_count = 1
            if self._pending_count < self.jump_confirm_frames:
                return  # not convinced yet — hold the frame

        self._pending_target = None
        self._pending_count = 0
        self.target_center_x = new_center

    def get_crop_box(self, force_snap=False):
        """Returns the (x1, y1, x2, y2) for the current frame."""
        if force_snap:
            self.current_center_x = self.target_center_x
        else:
            diff = self.target_center_x - self.current_center_x

            # SIMPLIFIED LOGIC:
            # 1. Is the target outside the safe zone?
            if abs(diff) > self.safe_zone_radius:
                # 2. If yes, move towards it slowly (Linear Speed)
                direction = 1 if diff > 0 else -1

                # Speed: slow steady pan; fast re-frame for huge jumps
                if abs(diff) > self.crop_width * 0.5:
                    speed = 15.0  # Fast re-frame (scene change)
                else:
                    speed = 3.0   # Slow, steady pan

                self.current_center_x += direction * speed

                # Check if we overshot (prevent oscillation)
                new_diff = self.target_center_x - self.current_center_x
                if (direction == 1 and new_diff < 0) or (direction == -1 and new_diff > 0):
                    self.current_center_x = self.target_center_x

            # If inside safe zone, DO NOTHING (Stationary Camera)

        # Clamp center
        half_crop = self.crop_width / 2

        if self.current_center_x - half_crop < 0:
            self.current_center_x = half_crop
        if self.current_center_x + half_crop > self.video_width:
            self.current_center_x = self.video_width - half_crop

        x1 = int(self.current_center_x - half_crop)
        x2 = int(self.current_center_x + half_crop)

        x1 = max(0, x1)
        x2 = min(self.video_width, x2)

        y1 = 0
        y2 = self.video_height

        return x1, y1, x2, y2
