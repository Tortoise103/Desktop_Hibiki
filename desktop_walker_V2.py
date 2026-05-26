import sys
import random
import math
from abc import ABC, abstractmethod

from PyQt5.QtCore import Qt, QTimer, QPoint, QElapsedTimer
from PyQt5.QtGui import QPixmap, QTransform, QRegion, QCursor
from PyQt5.QtWidgets import QApplication, QLabel, QMainWindow

# ==========================================
# 설정값 — 여기만 수정하면 됨
# ==========================================
IMG_PATH       = "fake_hibiki_transparent.png"
PANIC_IMG_PATH = "fake_hibiki_panic_transparent.png"
BLINK_IMG_PATH = "fake_hibiki_clicked_transparent.png"
HAPPY_IMG_PATH = "fake_hibiki_happy_transparent.png"
MASCOT_WIDTH   = 200

# 60Hz 기준 수치 (V1 대비 ÷3)
WALK_SPEED_MIN = 1.17
WALK_SPEED_MAX = 2.0

LOOK_CHANCE    = 0.0027      # 20Hz 기준 0.008과 동일한 평균 발생 주기
LOOK_TURNS_MIN = 1
LOOK_TURNS_MAX = 4
LOOK_HOLD_MIN  = 36          # 12틱 × 3
LOOK_HOLD_MAX  = 105         # 35틱 × 3

GRAVITY        = 0.77        # 2.3 / 3
BOUNCE_DAMPING = 0.15
MIN_BOUNCE_VY  = 0.67        # 2.0 / 3

CLICK_MAX_MS   = 120         # 이 시간(ms) 이하 + 거의 안 움직였을 때만 클릭
CLICK_MOVE_PX  = 4

DRAG_VX_MULT   = 2.1
DRAG_VY_MULT   = 2.7

# 패닉 도망 속도 (고정값, 커서 이속 무관)
PANIC_RUN_SPEED = 6.0        # px/틱 @ 60Hz

CEILING_Y      = 30
CEILING_BOUNCE = 0.6

UNSTUCK_DIST   = 60

# Hz
TICK_HZ        = 60
# ==========================================

S_WALK     = "WALK"
S_LOOK     = "LOOK"
S_DRAG     = "DRAG"
S_FALL     = "FALL"
S_STUNNED  = "STUNNED"
S_PANIC    = "PANIC"
S_CORNERED = "CORNERED"
S_FOLLOW   = "FOLLOW"
S_UNSTUCK  = "UNSTUCK"

PEACEFUL_STATES = (S_WALK, S_LOOK)
PANIC_STATES    = (S_PANIC, S_CORNERED)


# ============================================================
#  SpecialEvent
# ============================================================
class SpecialEvent(ABC):
    @abstractmethod
    def enter(self, mascot: "Spiki") -> None: ...
    @abstractmethod
    def tick(self, mascot: "Spiki") -> None: ...
    @abstractmethod
    def timer_expired(self, mascot: "Spiki") -> None: ...
    @property
    @abstractmethod
    def active_states(self) -> tuple: ...


class PanicEvent(SpecialEvent):
    """
    커서를 피해 도망. 벽에 몰리면 부들부들 떨다가 커서가 멀어지면 다시 도망.

    Parameters
    ----------
    duration      : 패닉 지속 시간 (초)
    speed_min     : 최소 도망 속도 (px/틱, 60Hz 기준)
    cursor_bonus  : 커서 속도 대비 배율
    tremble_amp   : 코너드 시 상하 진동 폭 (px)
    panic_img     : 패닉 표정 QPixmap
    """
    def __init__(self, duration=7.0, speed_min=6.0,
                 tremble_amp=3, panic_img=None):
        self.duration     = duration
        self.speed_min    = speed_min
        self.tremble_amp  = tremble_amp
        self.panic_img    = panic_img

    @property
    def active_states(self):
        return (S_PANIC, S_CORNERED)

    def enter(self, mascot):
        mascot.state = S_PANIC
        mascot.event_ticks_left = int(self.duration * TICK_HZ)
        mascot._tremble_tick = 0
        mascot._render()

    def tick(self, mascot):
        if mascot.state == S_PANIC:
            self._do_panic(mascot)
        elif mascot.state == S_CORNERED:
            self._do_cornered(mascot)

    def timer_expired(self, mascot):
        mascot._enter_peaceful()

    def _do_panic(self, mascot):
        cursor_x  = QCursor.pos().x()
        char_cx   = mascot.x() + MASCOT_WIDTH // 2
        flee_dir  = -1 if cursor_x > char_cx else 1

        if flee_dir != mascot.direction:
            mascot.direction = flee_dir
            mascot._render()

        next_x     = mascot.x() + self.speed_min * mascot.direction
        left_wall  = mascot.screen_x
        right_wall = mascot.screen_x + mascot.screen_w - MASCOT_WIDTH

        if next_x <= left_wall:
            next_x = left_wall
            mascot.state = S_CORNERED
            mascot._render()
        elif next_x >= right_wall:
            next_x = right_wall
            mascot.state = S_CORNERED
            mascot._render()

        mascot.move(int(next_x), mascot.floor_y)

    def _do_cornered(self, mascot):
        if mascot.event_ticks_left <= 0:
            mascot._enter_peaceful()
            return

        cursor_x = QCursor.pos().x()
        char_cx  = mascot.x() + MASCOT_WIDTH // 2
        face_dir = 1 if cursor_x > char_cx else -1

        if face_dir != mascot.direction:
            mascot.direction = face_dir
            mascot._render()

        # 60Hz에서 4틱 주기 → 진동 체감 동일
        mascot._tremble_tick += 1
        offset = self.tremble_amp if (mascot._tremble_tick % 12 < 6) else -self.tremble_amp
        mascot.move(mascot.x(), mascot.floor_y + offset)

        if abs(cursor_x - char_cx) > 300:
            mascot.state = S_PANIC


class FollowEvent(SpecialEvent):
    """
    커서를 졸졸 따라다님. dead_zone 내에 들어오면 멈추고 바라봄.

    Parameters
    ----------
    duration   : 팔로우 지속 시간 (초)
    speed      : 따라가는 속도 (px/틱, 60Hz 기준)
    dead_zone  : 이 거리 이내면 멈춤 (px)
    follow_img : 팔로우 표정 QPixmap
    """
    def __init__(self, duration=5.0, speed=2.33, dead_zone=40, follow_img=None):
        self.duration   = duration
        self.speed      = speed
        self.dead_zone  = dead_zone
        self.follow_img = follow_img

    @property
    def active_states(self):
        return (S_FOLLOW,)

    def enter(self, mascot):
        mascot.state = S_FOLLOW
        mascot.event_ticks_left = int(self.duration * TICK_HZ)
        mascot._render()

    def tick(self, mascot):
        cursor_x = QCursor.pos().x()
        char_cx  = mascot.x() + MASCOT_WIDTH // 2
        dist     = cursor_x - char_cx

        if abs(dist) <= self.dead_zone:
            face_dir = 1 if dist > 0 else -1
            if face_dir != mascot.direction:
                mascot.direction = face_dir
                mascot._render()
            return

        move_dir = 1 if dist > 0 else -1
        if move_dir != mascot.direction:
            mascot.direction = move_dir
            mascot._render()

        next_x = mascot.x() + self.speed * mascot.direction
        lo = mascot.screen_x
        hi = mascot.screen_x + mascot.screen_w - MASCOT_WIDTH
        next_x = max(lo, min(next_x, hi))
        mascot.move(int(next_x), mascot.floor_y)

    def timer_expired(self, mascot):
        mascot._enter_peaceful()


# ============================================================
#  ClickReaction
# ============================================================
class ClickReaction:
    """
    클릭(좌/우) 한 번에 대한 반응과 특수 이벤트 발동 조건.

    Parameters
    ----------
    reaction_img    : 클릭 직후 표정 QPixmap
    squish_scale    : 납작 세로 비율 (1.0=원본, 0.8=20% 납작)
    squish_ticks    : 납작 효과 유지 틱 수 (60Hz 기준)
    trigger_count   : 특수 이벤트 발동 최소 횟수
    trigger_window  : 카운트 유효 시간 (초)
    special_event   : SpecialEvent 인스턴스
    """
    def __init__(self, reaction_img=None, squish_scale=0.85, squish_ticks=12,
                 trigger_count=7, trigger_window=5.0, special_event=None):
        self.reaction_img   = reaction_img
        self.squish_scale   = squish_scale
        self.squish_ticks   = squish_ticks
        self.trigger_count  = trigger_count
        self.trigger_window = trigger_window
        self.special_event  = special_event

        self._times = []
        self._timer = QElapsedTimer()
        self._timer.start()

    def on_click(self, mascot) -> bool:
        mascot._reaction_squish_scale = self.squish_scale
        mascot._reaction_squish_ticks = self.squish_ticks
        mascot._active_reaction       = self
        mascot._render()

        if self.special_event is None:
            return False

        now    = self._timer.elapsed()
        cutoff = now - int(self.trigger_window * 1000)
        self._times.append(now)
        self._times = [t for t in self._times if t >= cutoff]

        if len(self._times) >= self.trigger_count:
            self._times.clear()
            self.special_event.enter(mascot)
            return True
        return False

    def clear(self):
        self._times.clear()


# ============================================================
#  Spiki
# ============================================================
class Spiki(QMainWindow):
    def __init__(self):
        super().__init__()

        self.state     = S_WALK
        self.direction = random.choice([-1, 1])
        self.speed     = random.uniform(WALK_SPEED_MIN, WALK_SPEED_MAX)
        self.tick      = 0

        self.look_ticks_left = 0
        self.look_turns_left = 0

        self.drag_offset    = QPoint()
        self.prev_mouse     = None
        self.vel_x          = 0.0
        self.vel_y          = 0.0
        self.drag_move_dist = 0.0

        # 드래그/낙하 중 실제 이동 방향 자동 갱신용 (리눅스 버전 포팅)
        self.prev_pos = None

        self.press_timer = QElapsedTimer()

        self.event_ticks_left = 0
        self.active_event: SpecialEvent | None = None

        self.cursor_speed   = 0.0
        self._prev_cursor_x = None

        self._tremble_tick = 0

        self._reaction_squish_scale = 1.0
        self._reaction_squish_ticks = 0
        self._active_reaction: ClickReaction | None = None

        self.blink_ticks_left   = 0
        self.stunned_ticks_left = 0

        self.unstuck_target_x = 0

        self._init_ui()
        self._setup_reactions()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(1000 // TICK_HZ)   # 60Hz → 16ms

    # ── UI 초기화 ────────────────────────────────────────────
    def _init_ui(self):
        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.label = QLabel(self)

        self.raw_pixmap = QPixmap(IMG_PATH)
        if self.raw_pixmap.isNull():
            print(f"[오류] 이미지를 찾을 수 없어요: {IMG_PATH}")
            sys.exit(1)

        def _load(path, name):
            px = QPixmap(path)
            if px.isNull():
                print(f"[경고] {name} 이미지 없음: {path} — 기본 이미지로 대체")
                return None
            return px

        self._panic_pixmap = _load(PANIC_IMG_PATH, "패닉")
        self._blink_pixmap = _load(BLINK_IMG_PATH, "블링크")
        self._happy_pixmap = _load(HAPPY_IMG_PATH, "해피")

        screen = QApplication.primaryScreen().availableGeometry()
        self.screen_x = screen.x()
        self.screen_w = screen.width()
        self.floor_y  = screen.y() + screen.height() - MASCOT_WIDTH - 2

        self._build_pixmap_cache()

        self.move(self.screen_x + self.screen_w // 2 - MASCOT_WIDTH // 2, self.floor_y)
        self._render()

    def _build_pixmap_cache(self):
        def _make_pair(px):
            if px is None:
                return None, None
            base = px.scaledToWidth(MASCOT_WIDTH, Qt.SmoothTransformation)
            flip = base.transformed(QTransform().scale(-1, 1), Qt.SmoothTransformation)
            return base, flip   # [0]=왼쪽(dir=-1), [1]=오른쪽(dir=1)

        self._cache = {
            "raw":   _make_pair(self.raw_pixmap),
            "blink": _make_pair(self._blink_pixmap),
            "panic": _make_pair(self._panic_pixmap),
            "happy": _make_pair(self._happy_pixmap),
        }
        self._last_render_key = None

    def _setup_reactions(self):
        """ClickReaction 설정 — 이미지/수치/이벤트 변경 시 여기만 수정."""
        self.left_reaction = ClickReaction(
            reaction_img   = self._blink_pixmap,
            squish_scale   = 0.80,
            squish_ticks   = 12,    # 0.2초 @ 60Hz
            trigger_count  = 7,
            trigger_window = 5.0,
            special_event  = PanicEvent(
                duration     = 5.0,
                speed_min    = PANIC_RUN_SPEED,
                tremble_amp  = 3,
                panic_img    = self._panic_pixmap,
            ),
        )

        self.right_reaction = ClickReaction(
            reaction_img   = self._happy_pixmap,
            squish_scale   = 0.92,
            squish_ticks   = 9,     # 0.15초 @ 60Hz
            trigger_count  = 7,
            trigger_window = 6.0,
            special_event  = FollowEvent(
                duration   = 5.0,
                speed      = 2.33,
                dead_zone  = 40,
                follow_img = self._happy_pixmap,
            ),
        )

    # ── 렌더링 ───────────────────────────────────────────────
    def _render(self, blink=False):
        # 이미지 키 결정
        if blink and self._blink_pixmap:
            img_key = "blink"
        elif self.state == S_DRAG and self._blink_pixmap:
            # 드래그 중에는 계속 blink 이미지 유지
            img_key = "blink"
        elif self.active_event is not None and self.state in self.active_event.active_states:
            if isinstance(self.active_event, PanicEvent) and self.active_event.panic_img:
                img_key = "panic"
            elif isinstance(self.active_event, FollowEvent) and self.active_event.follow_img:
                img_key = "happy"
            else:
                img_key = "raw"
        elif self._reaction_squish_ticks > 0 and self._active_reaction \
                and self._active_reaction.reaction_img:
            if self._active_reaction.reaction_img is self._blink_pixmap:
                img_key = "blink"
            elif self._active_reaction.reaction_img is self._happy_pixmap:
                img_key = "happy"
            else:
                img_key = "raw"
        else:
            img_key = "raw"

        squishing  = self._reaction_squish_ticks > 0
        render_key = (img_key, self.direction, squishing,
                      self._reaction_squish_scale if squishing else 1.0)

        # 변화 없으면 y 위치만 보정 후 스킵
        if not squishing and render_key == self._last_render_key:
            if self.state not in (S_FALL, S_DRAG):
                self.move(self.x(), self.floor_y)
            return
        self._last_render_key = render_key

        pair = self._cache.get(img_key, self._cache["raw"])
        if pair[0] is None:
            pair = self._cache["raw"]

        dir_idx = 1 if self.direction == 1 else 0

        if squishing:
            src_raw = {
                "blink": self._blink_pixmap,
                "happy": self._happy_pixmap,
                "panic": self._panic_pixmap,
                "raw":   self.raw_pixmap,
            }.get(img_key, self.raw_pixmap) or self.raw_pixmap

            base = src_raw.scaledToWidth(MASCOT_WIDTH, Qt.SmoothTransformation)
            t = QTransform()
            if self.direction == 1:
                t.scale(-1, 1)
            t.scale(1, self._reaction_squish_scale)
            scaled     = base.transformed(t, Qt.SmoothTransformation)
            original_h = base.height()
        else:
            scaled     = pair[dir_idx]
            original_h = scaled.height()

        self.label.setPixmap(scaled)
        self.label.resize(scaled.width(), scaled.height())
        self.resize(scaled.width(), scaled.height())
        mask = scaled.mask()
        if not mask.isNull():
            self.setMask(QRegion(mask))

        if self.state not in (S_FALL, S_DRAG):
            y_offset = original_h - scaled.height() if squishing else 0
            self.move(self.x(), self.floor_y + y_offset)

    # ── Peaceful 진입 ─────────────────────────────────────────
    def _enter_peaceful(self):
        self._reaction_squish_ticks = 0
        self.active_event           = None
        self.event_ticks_left       = 0
        self.move(self.x(), self.floor_y)
        if random.random() < 0.4:
            self._enter_look()
        else:
            self.speed = random.uniform(WALK_SPEED_MIN, WALK_SPEED_MAX)
            self.state = S_WALK
        self._render()

    # ── 마우스 이벤트 ─────────────────────────────────────────
    def _is_special_active(self):
        return self.active_event is not None and self.state in self.active_event.active_states

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.state          = S_DRAG
            self.drag_offset    = e.pos()
            self.prev_mouse     = e.globalPos()
            self.vel_x          = 0.0
            self.vel_y          = 0.0
            self.drag_move_dist = 0.0
            self.press_timer.start()
            # 들어올리는 순간 blink 이미지로 전환
            self._render(blink=True)

        elif e.button() == Qt.RightButton:
            if self._is_special_active():
                return
            triggered = self.right_reaction.on_click(self)
            if triggered:
                self.active_event = self.right_reaction.special_event
                self.left_reaction.clear()

    def mouseMoveEvent(self, e):
        if self.state == S_DRAG:
            new_pos = e.globalPos() - self.drag_offset
            if self.prev_mouse is not None:
                dp = e.globalPos() - self.prev_mouse
                self.drag_move_dist += math.hypot(dp.x(), dp.y())
                self.vel_x = dp.x() * DRAG_VX_MULT
                self.vel_y = dp.y() * DRAG_VY_MULT
            self.prev_mouse = e.globalPos()
            self.move(new_pos)

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton or self.state != S_DRAG:
            return

        elapsed  = self.press_timer.elapsed()
        is_click = self.drag_move_dist <= CLICK_MOVE_PX and elapsed < CLICK_MAX_MS

        if is_click:
            self.vel_x = 0.0
            self.vel_y = 0.0

            if self._is_special_active():
                self._reaction_squish_ticks = self.left_reaction.squish_ticks
                self._reaction_squish_scale = self.left_reaction.squish_scale
                self._active_reaction       = self.left_reaction
                self._render(blink=True)
                return

            triggered = self.left_reaction.on_click(self)
            if triggered:
                self.active_event = self.left_reaction.special_event
                self.right_reaction.clear()
                return

            self._render(blink=True)
            self.blink_ticks_left   = 18   # 0.3초 @ 60Hz
            self.state              = S_STUNNED
            self.stunned_ticks_left = 30   # 0.5초 @ 60Hz
        else:
            self.state = S_FALL
            if self.vel_x > 0:
                self.direction = 1
            elif self.vel_x < 0:
                self.direction = -1
            self._render()

    # ── 메인 루프 ─────────────────────────────────────────────
    def _tick(self):
        self.tick += 1

        cur_x = self.x()
        cur_y = self.y()

        # 드래그/낙하 중 실제 이동 방향으로 캐릭터 방향 자동 갱신 (리눅스 버전 포팅)
        if self.prev_pos is not None and self.state in (S_DRAG, S_FALL):
            dx = cur_x - self.prev_pos.x()
            if dx > 2 and self.direction != 1:
                self.direction = 1
                self._render()
            elif dx < -2 and self.direction != -1:
                self.direction = -1
                self._render()
        self.prev_pos = QPoint(cur_x, cur_y)

        # 커서 속도 추적
        cur_cx = QCursor.pos().x()
        if self._prev_cursor_x is not None:
            self.cursor_speed = abs(cur_cx - self._prev_cursor_x)
        self._prev_cursor_x = cur_cx

        # squish 카운트다운
        if self._reaction_squish_ticks > 0:
            self._reaction_squish_ticks -= 1
            if self._reaction_squish_ticks == 0:
                self._render()

        # blink 카운트다운
        if self.blink_ticks_left > 0:
            self.blink_ticks_left -= 1
            if self.blink_ticks_left == 0:
                self._render()

        # 이벤트 타이머 카운트다운
        if self.event_ticks_left > 0:
            self.event_ticks_left -= 1
            if self.event_ticks_left == 0 and self._is_special_active():
                self.active_event.timer_expired(self)
                return

        if self.state == S_WALK:
            self._do_walk()
        elif self.state == S_LOOK:
            self._do_look()
        elif self.state == S_FALL:
            self._do_fall()
        elif self.state == S_STUNNED:
            self._do_stunned()
        elif self.state == S_UNSTUCK:
            self._do_unstuck()
        elif self._is_special_active():
            self.active_event.tick(self)

    # ── WALK ─────────────────────────────────────────────────
    def _do_walk(self):
        next_x = self.x() + self.speed * self.direction
        if next_x <= self.screen_x:
            self._enter_unstuck(target_x=self.screen_x + UNSTUCK_DIST)
            return
        elif next_x >= self.screen_x + self.screen_w - MASCOT_WIDTH:
            self._enter_unstuck(target_x=self.screen_x + self.screen_w - MASCOT_WIDTH - UNSTUCK_DIST)
            return
        self.move(int(next_x), self.floor_y)
        if random.random() < LOOK_CHANCE:
            self._enter_look()

    # ── UNSTUCK ───────────────────────────────────────────────
    def _enter_unstuck(self, target_x):
        self.state = S_UNSTUCK
        lo = self.screen_x
        hi = self.screen_x + self.screen_w - MASCOT_WIDTH
        self.unstuck_target_x = max(lo, min(target_x, hi))
        self.direction = 1 if self.unstuck_target_x > self.x() else -1
        self.speed = random.uniform(WALK_SPEED_MIN, WALK_SPEED_MAX)
        self._render()

    def _do_unstuck(self):
        next_x  = self.x() + self.speed * self.direction
        reached = (self.direction == 1  and next_x >= self.unstuck_target_x) or \
                  (self.direction == -1 and next_x <= self.unstuck_target_x)
        if reached:
            self.move(int(self.unstuck_target_x), self.floor_y)
            self._enter_peaceful()
        else:
            self.move(int(next_x), self.floor_y)

    # ── LOOK ─────────────────────────────────────────────────
    def _enter_look(self):
        self.state = S_LOOK
        self.look_turns_left = random.randint(LOOK_TURNS_MIN, LOOK_TURNS_MAX)
        self.look_ticks_left = random.randint(LOOK_HOLD_MIN, LOOK_HOLD_MAX)

    def _do_look(self):
        self.look_ticks_left -= 1
        if self.look_ticks_left <= 0:
            if self.look_turns_left <= 0:
                self._enter_peaceful()
                return
            self.direction *= -1
            self._render()
            self.look_turns_left -= 1
            self.look_ticks_left = random.randint(LOOK_HOLD_MIN, LOOK_HOLD_MAX)

    # ── STUNNED ───────────────────────────────────────────────
    def _do_stunned(self):
        self.stunned_ticks_left -= 1
        if self.stunned_ticks_left <= 0:
            self._enter_peaceful()

    # ── FALL ─────────────────────────────────────────────────
    def _do_fall(self):
        self.vel_y += GRAVITY
        next_x = self.x() + self.vel_x
        next_y = self.y() + self.vel_y

        left_wall  = self.screen_x
        right_wall = self.screen_x + self.screen_w - MASCOT_WIDTH

        # 벽 충돌: 속도 방향 기반
        if self.vel_x < 0 and self.x() <= left_wall:
            self.vel_x = abs(self.vel_x) * 0.6
            next_x = self.x() + self.vel_x
            self.direction = 1
            self._render()
        elif self.vel_x > 0 and self.x() >= right_wall:
            self.vel_x = -abs(self.vel_x) * 0.6
            next_x = self.x() + self.vel_x
            self.direction = -1
            self._render()

        # 천장 반발
        if self.vel_y < 0 and self.y() <= CEILING_Y:
            self.vel_y = abs(self.vel_y) * CEILING_BOUNCE
            next_y = self.y() + self.vel_y

        next_x = max(left_wall, min(next_x, right_wall))

        # 바닥 착지
        if next_y >= self.floor_y:
            next_y = self.floor_y
            self.vel_x *= 0.7

            if abs(self.vel_y) < MIN_BOUNCE_VY:
                if self.x() <= left_wall:
                    self.direction = 1
                elif self.x() >= right_wall:
                    self.direction = -1
                elif self.vel_x > 0:
                    self.direction = 1
                elif self.vel_x < 0:
                    self.direction = -1
                self.vel_x = 0.0
                self.vel_y = 0.0
                self.move(int(next_x), int(next_y))
                if self._is_special_active():
                    self._render()
                else:
                    self._enter_peaceful()
                return
            else:
                self.vel_y = -self.vel_y * BOUNCE_DAMPING

        self.move(int(next_x), int(next_y))


def main():
    app = QApplication(sys.argv)
    spiki = Spiki()
    spiki.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
