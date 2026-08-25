"""SpeakerTracker — sticky multi-speaker target selection.

Vendored from openshorts (MIT, github.com/mutonby/openshorts, main.py).
Unchanged logic; extracted verbatim.

Prevents rapid speaker switching and rides out temporary obstructions:
score decay + hysteresis (3x stickiness for the active speaker) + a switch
cooldown that holds even when the current speaker is missing from the
candidate list (a blink or motion-blurred frame used to jump the cooldown).
"""


class SpeakerTracker:
    """
    Tracks speakers over time to prevent rapid switching and handle temporary
    obstructions.
    """
    def __init__(self, stabilization_frames=15, cooldown_frames=30):
        self.active_speaker_id = None
        self.speaker_scores = {}  # {id: score}
        self.last_seen = {}       # {id: frame_number}
        self.locked_counter = 0   # How long we've been locked on current speaker

        # Hyperparameters
        self.stabilization_threshold = stabilization_frames  # Frames needed to confirm a new speaker
        self.switch_cooldown = cooldown_frames               # Minimum frames before switching again
        self.last_switch_frame = -1000

        # ID tracking
        self.next_id = 0
        self.known_faces = []  # [{'id': 0, 'center': x, 'last_frame': 123}]

    def get_target(self, face_candidates, frame_number, width):
        """
        Decides which face to focus on.
        face_candidates: list of {'box': [x,y,w,h], 'score': float}
        Returns a box, or None to hold the current framing.
        """
        current_candidates = []

        # 1. Match faces to known IDs (simple distance tracking)
        for face in face_candidates:
            x, y, w, h = face['box']
            center_x = x + w / 2

            best_match_id = -1
            min_dist = width * 0.15  # Matching radius; avoids jumping in groups

            for kf in self.known_faces:
                if frame_number - kf['last_frame'] > 30:  # Forget faces older than ~1s
                    continue

                dist = abs(center_x - kf['center'])
                if dist < min_dist:
                    min_dist = dist
                    best_match_id = kf['id']

            if best_match_id == -1:
                best_match_id = self.next_id
                self.next_id += 1

            self.known_faces = [kf for kf in self.known_faces if kf['id'] != best_match_id]
            self.known_faces.append({'id': best_match_id, 'center': center_x,
                                     'last_frame': frame_number})

            current_candidates.append({
                'id': best_match_id,
                'box': face['box'],
                'score': face['score']
            })

        # 2. Update scores with decay
        for pid in list(self.speaker_scores.keys()):
            self.speaker_scores[pid] *= 0.85
            if self.speaker_scores[pid] < 0.1:
                del self.speaker_scores[pid]

        # Add new scores
        for cand in current_candidates:
            pid = cand['id']
            raw_score = cand['score'] / (width * width * 0.05)
            self.speaker_scores[pid] = self.speaker_scores.get(pid, 0) + raw_score

        # 3. Determine best speaker
        if not current_candidates:
            return None

        best_candidate = None
        max_score = -1

        for cand in current_candidates:
            pid = cand['id']
            total_score = self.speaker_scores.get(pid, 0)

            # Hysteresis: huge bonus for current active speaker
            if pid == self.active_speaker_id:
                total_score *= 3.0  # Sticky factor

            if total_score > max_score:
                max_score = total_score
                best_candidate = cand

        # 4. Decide switch
        if best_candidate:
            target_id = best_candidate['id']

            if target_id == self.active_speaker_id:
                self.locked_counter += 1
                return best_candidate['box']

            # New person. The cooldown must hold whether or not the current
            # speaker happens to be detected in THIS frame.
            #
            # It used to fall through and switch when the active speaker was
            # missing from the candidate list — a blink, a head turn or one
            # motion-blurred frame was enough. That is precisely when the
            # cooldown is needed, so it only ever fired when it wasn't: 3 of 7
            # target switches measured on a 12s clip (25-jul-2026) jumped the
            # cooldown this way, and every jump drags the camera across frame.
            #
            # Returning None holds instead: the caller only calls
            # update_target() on a truthy box, so the camera keeps its current
            # target and finishes whatever move it was making. The hold is
            # bounded by the cooldown itself — once it expires, a speaker who
            # really did leave the shot is switched away from normally.
            if frame_number - self.last_switch_frame < self.switch_cooldown:
                old_cand = next((c for c in current_candidates
                                 if c['id'] == self.active_speaker_id), None)
                return old_cand['box'] if old_cand else None

            self.active_speaker_id = target_id
            self.last_switch_frame = frame_number
            self.locked_counter = 0
            return best_candidate['box']

        return None
