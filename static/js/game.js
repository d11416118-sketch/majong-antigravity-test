import { Renderer } from "./renderer.js";
import { GameCharacters } from "./game-characters.js";
import { THEMES, applyDocumentTheme, getTheme, normalizeThemeId } from "./themes.js";

const BASE_TOKEN_KEY = "tw_mahjong_token";
const CHAT_MAX_IMAGE_MB = 10;
const CHAT_MAX_IMAGE_BYTES = CHAT_MAX_IMAGE_MB * 1024 * 1024;
const MARK_VALUES = ["無", "1", "2", "3"];

export class GameClient {
    constructor(socket) {
        this.socket = socket;
        this.account = null;
        this.profile = null;
        this.selfHistory = [];
        this.roomId = null;
        this.latestState = null;
        this.selectedTileIndex = null;
        this.myTurn = false;
        this.aiEnabled = false;
        this.autoPlayEnabled = false;
        this.pendingQueueAfterLeave = null;
        this.beginnerEnabled = window.localStorage.getItem("tw_mahjong_beginner") === "1";
        this.infoPanelOpen = window.localStorage.getItem("tw_mahjong_info_open") === "1";
        this.chatPanelOpen = window.localStorage.getItem("tw_mahjong_chat_open") === "1";
        this.trackerMarks = {};
        this.hintRequestKey = "";
        this.animationChain = Promise.resolve();
        this.animationGeneration = 0;
        this.discardActionSeq = 0;
        this.pendingLocalDiscardIds = new Set();
        this.latestStateSeq = 0;
        this.serverClockOffset = 0;
        this.countdownTimer = null;
        this.rewardState = null;
        this.socialState = null;
        this.onlineRewardTimer = null;
        this.onlineRewardCountdownTimer = null;
        this.params = new URLSearchParams(window.location.search);
        this.clientId = this.params.get("client");
        this.tokenKey = this.clientId ? `${BASE_TOKEN_KEY}_${this.clientId}` : BASE_TOKEN_KEY;
        this.autojoinRoomId = this.params.get("autojoin");
        this.didAutojoin = false;
        this.pendingTestRoom = false;
        this.pendingTestWindows = [];

        if (this.params.get("fresh") === "1") {
            window.localStorage.removeItem(this.tokenKey);
        }

        this.renderer = new Renderer({
            onTileClick: (index) => this.selectTile(index),
            onAction: (action) => this.sendAction(action),
            onDiscard: (index) => this.discardTile(index),
            onTrackerMark: (tile) => this.cycleTrackerMark(tile),
            onNextRound: () => this.requestNextRound(),
            onRematch: () => this.requestRematch(),
            onLeaveRoom: (queueMode) => this.leaveRoom(queueMode),
            onSticker: (stickerId) => this.sendSticker(stickerId),
        });
        this.gameCharacters = new GameCharacters({
            canvas: document.getElementById("game-characters-canvas"),
            stage: document.getElementById("table-stage"),
        });

        this.bindDom();
        this.bindSocket();
        this.applyUrlHints();
        this.renderPanelToggle();
        this.renderChatToggle();
        this.renderBeginnerToggle();
    }

    bindDom() {
        document.getElementById("btn-login").addEventListener("click", () => this.login());
        document.getElementById("btn-register").addEventListener("click", () => this.register());

        const togglePasswordBtn = document.getElementById("toggle-password-btn");
        if (togglePasswordBtn) {
            togglePasswordBtn.addEventListener("click", () => {
                const pwdInput = document.getElementById("input-password");
                const isPassword = pwdInput.type === "password";
                pwdInput.type = isPassword ? "text" : "password";
                togglePasswordBtn.querySelector(".eye-icon-off").classList.toggle("hidden", isPassword);
                togglePasswordBtn.querySelector(".eye-icon-on").classList.toggle("hidden", !isPassword);
            });
        }

        document.getElementById("input-username").addEventListener("input", () => this.hideAuthError());
        document.getElementById("input-password").addEventListener("input", () => this.hideAuthError());

        document.getElementById("btn-matchmaking").addEventListener("click", () => this.joinMatchmaking());
        document.getElementById("btn-leave-matchmaking").addEventListener("click", () => this.leaveMatchmaking());
        document.getElementById("btn-ranked").addEventListener("click", () => this.joinRanked());
        document.getElementById("btn-leave-ranked").addEventListener("click", () => this.leaveRanked());
        document.getElementById("btn-custom-create").addEventListener("click", () => this.createRoom());
        document.getElementById("btn-test-room").addEventListener("click", () => this.createTestRoom());
        document.getElementById("btn-custom-join").addEventListener("click", () => this.joinRoom());
        document.getElementById("btn-daily-checkin").addEventListener("click", () => this.claimDailyCheckin());
        document.getElementById("btn-display-name").addEventListener("click", () => this.updateDisplayName());
        document.getElementById("btn-friend-add").addEventListener("click", () => this.sendFriendRequest());
        document.getElementById("btn-history-search").addEventListener("click", () => this.requestHistory());
        document.getElementById("btn-history-self").addEventListener("click", () => this.requestHistory(true));
        document.getElementById("profile-card").addEventListener("click", () => this.openProfileHome());
        document.getElementById("profile-home-close").addEventListener("click", () => this.closeProfileHome());
        document.getElementById("profile-home-overlay").addEventListener("click", (event) => {
            if (event.target.id === "profile-home-overlay") {
                this.closeProfileHome();
            }
        });
        document.getElementById("theme-options").addEventListener("click", (event) => {
            const button = event.target.closest("button[data-theme-id]");
            if (button) {
                this.updateTheme(button.dataset.themeId);
            }
        });
        window.addEventListener("keydown", (event) => {
            if (event.key === "Escape") {
                this.closeProfileHome();
            }
        });
        window.addEventListener("lobby-character-change", (event) => this.updateCharacter(event.detail?.id));
        document.getElementById("view-mode-toggle").addEventListener("click", () => this.toggleViewMode());
        document.getElementById("ai-toggle").addEventListener("click", () => this.toggleAi());
        document.getElementById("leave-room-button").addEventListener("click", () => this.leaveRoom());
        document.getElementById("dissolve-room-button").addEventListener("click", () => this.dissolveRoom());
        document.getElementById("lobby-leave-room-button").addEventListener("click", () => this.leaveRoom());
        document.getElementById("lobby-dissolve-room-button").addEventListener("click", () => this.dissolveRoom());
        document.getElementById("knowledge-toggle").addEventListener("click", () => this.toggleKnowledgePanel());
        document.getElementById("chat-toggle").addEventListener("click", () => this.toggleChatPanel());
        document.getElementById("beginner-toggle").addEventListener("click", () => this.toggleBeginnerMode());
        document.getElementById("chat-send-button").addEventListener("click", () => this.sendChatText());
        document.getElementById("chat-image-button").addEventListener("click", () => document.getElementById("chat-image-input").click());
        document.getElementById("chat-image-input").addEventListener("change", (event) => this.sendChatImage(event));
        document.getElementById("chat-input").addEventListener("keydown", (event) => {
            if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                this.sendChatText();
            }
        });
    }

    bindSocket() {
        this.socket.on("connect", () => {
            const token = window.localStorage.getItem(this.tokenKey);
            if (token) {
                this.socket.emit("resume_session", { token });
            }
        });

        this.socket.on("auth_success", (data) => {
            this.account = data.account;
            this.profile = data.profile;
            this.rewardState = data.rewards || null;
            this.socialState = data.social || null;
            this.loadTrackerMarks();
            window.localStorage.setItem(this.tokenKey, data.token);
            this.renderer.setIdentity(this.account.id);
            document.getElementById("auth-form").classList.add("hidden");
            this.renderHomeProfile();
            this.renderRewardState();
            this.renderSocialState();
            this.startOnlineRewardHeartbeat();
            this.showView("home");
            this.requestHistory(true);
            this.autoJoinFromUrl();
        });

        this.socket.on("auth_failed", () => {
            window.localStorage.removeItem(this.tokenKey);
            this.renderer.showToast("登入狀態已失效，請重新登入");
        });

        this.socket.on("room_created", (data) => {
            this.roomId = data.room_id;
            document.getElementById("lobby-room-id").textContent = this.roomId;
            if (this.pendingTestRoom) {
                this.openTestClientWindows(data.room_id);
                this.pendingTestRoom = false;
            }
        });

        this.socket.on("join_success", (data) => {
            this.roomId = data.room_id;
        });

        this.socket.on("rejoin_success", (data) => {
            this.roomId = data.room_id;
            this.renderer.showToast("已重新連回牌局");
        });

        this.socket.on("update_lobby", (data) => {
            this.roomId = data.room_id;
            this.updateRoomControls({ room_mode: data.mode, host_uid: data.host_uid });
            if (data.state === "LOBBY") {
                this.showView("lobby");
            } else {
                document.getElementById("game-room-id").textContent = this.roomId;
                this.showView("game");
            }
            this.renderer.renderLobby(data);
        });

        this.socket.on("game_start", (data) => {
            this.cancelGameAnimations();
            this.roomId = data.room_id || this.roomId;
            document.getElementById("game-room-id").textContent = this.roomId;
            this.renderer.hideEndGame();
            this.renderer.clearActionButtons();
            this.showView("game");
        });

        this.socket.on("game_event", (event) => {
            const actionId = String(event?.client_action_id || "");
            if (actionId && this.pendingLocalDiscardIds.delete(actionId)) {
                return;
            }
            this.enqueueGameEvent(event);
        });

        this.socket.on("game_update", (state) => {
            this.handleGameUpdate(state);
        });

        this.socket.on("request_action", (data) => {
            this.renderer.showActionButtons(data.actions || []);
        });

        this.socket.on("clear_actions", () => {
            this.renderer.clearActionButtons();
        });

        this.socket.on("decision_timer", (data) => {
            this.syncServerClock(data.server_time);
            this.startDecisionCountdown(data.deadline, data.seconds);
        });

        this.socket.on("decision_timer_clear", () => {
            this.clearDecisionCountdown();
        });

        this.socket.on("ai_status", (data) => {
            this.aiEnabled = Boolean(data.enabled);
            this.renderAiToggle();
        });

        this.socket.on("hint_reply", (data) => {
            if (this.myTurn && this.beginnerEnabled && Number.isInteger(data.tile_index)) {
                this.renderer.setRecommendedTileIndex(data.tile_index);
            }
        });

        this.socket.on("game_over", (data) => {
            this.cancelGameAnimations();
            this.clearDecisionCountdown();
            if (this.latestState) {
                this.latestState.room_state = data.match_ended ? "MATCH_ENDED" : "HAND_ENDED";
            }
            this.renderer.renderEndGame(data);
        });

        this.socket.on("match_over", (data) => {
            this.cancelGameAnimations();
            this.clearDecisionCountdown();
            if (this.latestState) {
                this.latestState.room_state = "MATCH_ENDED";
            }
            this.renderer.renderMatchOver(data);
        });

        this.socket.on("next_round_status", (data) => {
            this.renderer.renderNextRoundStatus(data);
        });

        this.socket.on("next_round_started", () => {
            this.cancelGameAnimations();
            this.renderer.showToast("新的一局開始");
        });

        this.socket.on("rematch_status", (data) => {
            this.renderer.renderRematchStatus(data);
        });

        this.socket.on("rematch_started", () => {
            this.renderer.showToast("同房新牌局開始");
        });

        this.socket.on("turn_timeout", (data) => {
            const player = this.latestState?.players?.find((item) => item.uid === data.uid);
            this.renderer.showToast(`${player?.name || "玩家"}逾時，系統隨機出牌`);
        });

        this.socket.on("auto_play_status", (data) => {
            if (data.enabled) {
                this.renderer.showToast("離線滿150秒，AI代理已接手");
            }
        });

        this.socket.on("room_left", () => {
            this.finishLeavingRoom("已離開牌桌");
        });

        this.socket.on("room_dissolved", (data) => {
            const mine = data.leaver_uid === this.account?.id;
            const message = mine
                ? (data.penalized ? "你已退出，本場按第4名結算" : "你已離開牌桌")
                : `${data.leaver_name || "玩家"}已退出，牌桌已解散`;
            this.finishLeavingRoom(message);
        });

        this.socket.on("matchmaking_status", (data) => {
            this.renderMatchmakingStatus(data);
        });

        this.socket.on("ranked_status", (data) => {
            this.renderRankedStatus(data);
        });

        this.socket.on("match_found", () => {
            this.renderer.showToast("配對成功");
        });

        this.socket.on("history_result", (data) => {
            this.renderHistoryResult(data);
        });

        this.socket.on("profile_update", (profile) => {
            this.profile = profile;
            this.renderHomeProfile();
        });

        this.socket.on("reward_state", (data) => {
            this.rewardState = data;
            this.renderRewardState();
        });

        this.socket.on("reward_claimed", (data) => {
            this.renderer.showToast(`獲得 ${data.coins || 0} 金幣`);
        });

        this.socket.on("social_state", (data) => {
            this.socialState = data;
            this.renderSocialState();
        });

        this.socket.on("social_notice", () => {
            this.renderer.showToast("好友狀態已更新");
        });

        this.socket.on("chat_history", (data) => {
            this.renderer.renderChatHistory(data.messages || []);
        });

        this.socket.on("chat_message", (message) => {
            this.renderer.appendChatMessage(message);
        });

        this.socket.on("server_error", (data) => {
            const viewLogin = document.getElementById("view-login");
            const errorMsg = document.getElementById("auth-error-message");
            if (viewLogin && !viewLogin.classList.contains("hidden") && errorMsg) {
                errorMsg.textContent = data.message || "伺服器錯誤";
                errorMsg.classList.remove("hidden");
            } else {
                this.renderer.showToast(data.message || "伺服器錯誤");
            }
        });
    }

    hideAuthError() {
        const errorMsg = document.getElementById("auth-error-message");
        if (errorMsg) {
            errorMsg.classList.add("hidden");
        }
    }

    applyUrlHints() {
        if (!this.autojoinRoomId) {
            return;
        }

        const roomInput = document.getElementById("input-room-code");
        if (roomInput) {
            roomInput.value = this.autojoinRoomId;
        }

        const usernameInput = document.getElementById("input-username");
        if (usernameInput && this.clientId && !usernameInput.value) {
            usernameInput.value = `test${this.clientId}_${Math.floor(Math.random() * 900 + 100)}`;
        }

        const passwordInput = document.getElementById("input-password");
        if (passwordInput && !passwordInput.value) {
            passwordInput.value = "123456";
        }
    }

    login() {
        this.socket.emit("login_account", this.authPayload());
    }

    register() {
        this.socket.emit("register_account", this.authPayload());
    }

    authPayload() {
        return {
            username: document.getElementById("input-username").value.trim(),
            password: document.getElementById("input-password").value,
        };
    }

    createRoom() {
        this.socket.emit("create_room", {
            track_stats: document.getElementById("custom-track-stats").checked,
            base_stake: Number(document.getElementById("custom-base-stake").value || 10),
        });
    }

    createTestRoom() {
        if (!this.account) {
            this.renderer.showToast("請先註冊或登入帳號");
            return;
        }

        this.pendingTestWindows = [2, 3, 4].map((clientId) => {
            const win = window.open("about:blank", `tw-mahjong-test-${clientId}`);
            if (win) {
                win.document.title = "台灣麻將測試分身";
                win.document.body.innerHTML = "<p style=\"font-family:sans-serif;padding:24px\">測試視窗準備中...</p>";
            }
            return { clientId, win };
        });

        if (this.roomId) {
            this.openTestClientWindows(this.roomId);
            return;
        }

        this.pendingTestRoom = true;
        this.socket.emit("create_room", { track_stats: false, base_stake: 10 });
    }

    openTestClientWindows(roomId) {
        const blocked = [];
        this.pendingTestWindows.forEach(({ clientId, win }) => {
            const url = new URL(window.location.pathname, window.location.origin);
            url.searchParams.set("client", String(clientId));
            url.searchParams.set("autojoin", roomId);
            url.searchParams.set("fresh", "1");

            if (win && !win.closed) {
                win.location.href = url.toString();
            } else {
                const opened = window.open(url.toString(), `tw-mahjong-test-${clientId}`);
                if (!opened) {
                    blocked.push(clientId);
                }
            }
        });

        if (blocked.length) {
            this.renderer.showToast(`有 ${blocked.length} 個測試視窗被擋住，請允許彈出視窗`);
        } else {
            this.renderer.showToast("已開啟 3 個測試視窗，請逐一註冊帳號");
        }
        this.pendingTestWindows = [];
    }

    autoJoinFromUrl() {
        if (!this.autojoinRoomId || this.didAutojoin) {
            return;
        }
        this.didAutojoin = true;
        this.socket.emit("join_room", { room_id: this.autojoinRoomId });
        this.renderer.showToast("登入後會自動加入測試房");
    }

    joinRoom() {
        const roomCode = document.getElementById("input-room-code").value.trim();
        if (roomCode.length !== 6) {
            this.renderer.showToast("房號需要 6 碼");
            return;
        }
        this.socket.emit("join_room", { room_id: roomCode });
    }

    joinMatchmaking() {
        this.socket.emit("join_matchmaking", {});
    }

    leaveMatchmaking() {
        this.socket.emit("leave_matchmaking", {});
    }

    joinRanked() {
        this.socket.emit("join_ranked", {});
    }

    leaveRanked() {
        this.socket.emit("leave_ranked", {});
    }

    claimDailyCheckin() {
        this.socket.emit("claim_daily_checkin", {});
    }

    updateDisplayName() {
        const input = document.getElementById("display-name-input");
        const displayName = input.value.trim();
        if (!displayName) {
            this.renderer.showToast("請輸入暱稱");
            return;
        }
        this.socket.emit("update_display_name", { display_name: displayName });
    }

    updateCharacter(characterId) {
        if (!this.account || !characterId || this.profile?.character_id === characterId) {
            return;
        }
        this.socket.emit("update_character", { character_id: characterId });
    }

    updateTheme(themeId) {
        if (!this.account) {
            return;
        }

        const normalized = normalizeThemeId(themeId);
        this.profile = { ...(this.profile || {}), theme_id: normalized };
        applyDocumentTheme(normalized);
        this.renderThemePicker();
        this.socket.emit("update_theme", { theme_id: normalized });
    }

    sendFriendRequest() {
        const input = document.getElementById("friend-username");
        const username = input.value.trim();
        if (!username) {
            this.renderer.showToast("請輸入玩家帳號");
            return;
        }
        this.socket.emit("send_friend_request", { username });
        input.value = "";
    }

    respondFriendRequest(requesterId, accept) {
        this.socket.emit("respond_friend_request", { requester_id: requesterId, accept });
    }

    removeFriend(friendId) {
        this.socket.emit("remove_friend", { friend_id: friendId });
    }

    startOnlineRewardHeartbeat() {
        clearInterval(this.onlineRewardTimer);
        clearInterval(this.onlineRewardCountdownTimer);
        this.socket.emit("online_reward_ping", {});
        this.onlineRewardTimer = setInterval(() => this.socket.emit("online_reward_ping", {}), 60_000);
        this.onlineRewardCountdownTimer = setInterval(() => {
            if (!this.rewardState || !Number.isFinite(Number(this.rewardState.online_next_seconds))) {
                return;
            }
            this.rewardState.online_next_seconds = Math.max(0, Number(this.rewardState.online_next_seconds) - 1);
            this.renderRewardState();
        }, 1000);
    }

    requestHistory(selfOnly = false) {
        const input = document.getElementById("history-username");
        const username = selfOnly ? "" : input.value.trim();
        if (selfOnly) {
            input.value = "";
        }
        this.socket.emit("request_history", { username });
    }

    renderHomeProfile() {
        const profile = this.profile || {};
        const displayName = profile.display_name || profile.username || this.account?.username || "---";
        const rankText = `${profile.rank_name || "一段"} ${Number(profile.rank_points || 0)} 分`;
        const recordText = `${profile.wins || 0} 勝 / ${profile.games_played || 0} 場`;
        const coinsText = Number(profile.coins || 0).toLocaleString("zh-TW");
        document.getElementById("home-player-name").textContent = displayName;
        document.getElementById("home-player-coins").textContent = coinsText;
        document.getElementById("home-player-record").textContent = recordText;
        document.getElementById("home-player-rank").textContent = rankText;
        document.getElementById("profile-card-name").textContent = displayName;
        document.getElementById("profile-card-rank").textContent = rankText;
        document.getElementById("profile-card-coins").textContent = `${coinsText} 金幣`;
        document.getElementById("profile-card-record").textContent = recordText;
        document.getElementById("profile-card-avatar").textContent = displayName.trim().slice(0, 1).toUpperCase() || "?";
        applyDocumentTheme(profile.theme_id || "classic");
        this.renderThemePicker();
        this.syncLobbyCharacter(profile.character_id || "default");
        this.renderProfileHome();
        const displayNameInput = document.getElementById("display-name-input");
        if (displayNameInput && !displayNameInput.value) {
            displayNameInput.value = profile.display_name || profile.username || "";
        }
    }

    openProfileHome() {
        this.renderProfileHome();
        const overlay = document.getElementById("profile-home-overlay");
        overlay.classList.remove("hidden");
        overlay.setAttribute("aria-hidden", "false");
    }

    closeProfileHome() {
        const overlay = document.getElementById("profile-home-overlay");
        if (!overlay || overlay.classList.contains("hidden")) {
            return;
        }
        overlay.classList.add("hidden");
        overlay.setAttribute("aria-hidden", "true");
    }

    renderProfileHome() {
        const profile = this.profile || {};
        const displayName = profile.display_name || profile.username || this.account?.username || "---";
        const rankName = profile.rank_name || "一段";
        const rankPoints = Number(profile.rank_points || 0);
        const rankFloor = Number(profile.rank_floor || 0);
        const nextRankPoints = profile.next_rank_points == null ? null : Number(profile.next_rank_points);
        const rankedGames = Number(profile.ranked_games || 0);
        const rankedWins = Number(profile.ranked_wins || 0);

        document.getElementById("profile-home-title").textContent = displayName;
        document.getElementById("profile-home-subtitle").textContent = `@${profile.username || this.account?.username || "---"}`;
        document.getElementById("profile-home-rank").textContent = `${rankName} · ${rankPoints} 分`;
        document.getElementById("profile-home-coins").textContent = Number(profile.coins || 0).toLocaleString("zh-TW");
        document.getElementById("profile-home-record").textContent = `${profile.wins || 0} 勝 / ${profile.games_played || 0} 場`;
        document.getElementById("profile-home-ranked-record").textContent = `${rankedWins} 勝 / ${rankedGames} 場`;
        document.getElementById("profile-home-character").textContent = profile.character_id === "flair" ? "Flair" : "預設";
        document.getElementById("profile-home-theme").textContent = getTheme(profile.theme_id).label;

        const progress = document.getElementById("profile-home-rank-progress");
        const progressText = document.getElementById("profile-home-rank-progress-text");
        if (nextRankPoints == null) {
            progress.style.width = "100%";
            progressText.textContent = `${rankPoints} 分 · 最高段位`;
        } else {
            const span = Math.max(1, nextRankPoints - rankFloor);
            const ratio = Math.max(0, Math.min(1, (rankPoints - rankFloor) / span));
            progress.style.width = `${Math.round(ratio * 100)}%`;
            progressText.textContent = `${rankPoints} / ${nextRankPoints} 分 · 距離下一段 ${Math.max(0, nextRankPoints - rankPoints)} 分`;
        }

        this.renderProfileHomeRecent();
    }

    renderThemePicker() {
        const list = document.getElementById("theme-options");
        const activeThemeId = normalizeThemeId(this.profile?.theme_id);
        const activeTheme = getTheme(activeThemeId);
        document.getElementById("theme-status").textContent = activeTheme.label;
        list.innerHTML = "";

        THEMES.forEach((theme) => {
            const button = document.createElement("button");
            button.type = "button";
            button.dataset.themeId = theme.id;
            button.className = "theme-option";
            button.classList.toggle("active", theme.id === activeThemeId);
            button.setAttribute("role", "radio");
            button.setAttribute("aria-checked", theme.id === activeThemeId ? "true" : "false");

            const swatch = document.createElement("i");
            swatch.className = "theme-swatch";
            swatch.style.setProperty("--swatch-color", theme.color);

            const copy = document.createElement("span");
            const label = document.createElement("strong");
            label.textContent = theme.label;
            const description = document.createElement("small");
            description.textContent = theme.description;
            copy.append(label, description);

            button.append(swatch, copy);
            list.appendChild(button);
        });
    }

    renderProfileHomeRecent() {
        const list = document.getElementById("profile-home-recent");
        list.innerHTML = "";
        const recent = (this.selfHistory || []).slice(0, 5);
        if (!recent.length) {
            const empty = document.createElement("div");
            empty.className = "profile-home-recent-empty";
            empty.textContent = "還沒有最近牌局";
            list.appendChild(empty);
            return;
        }

        recent.forEach((record) => {
            const item = document.createElement("article");
            item.className = "profile-home-recent-item";

            const title = document.createElement("div");
            title.className = "profile-home-recent-title";
            const mode = record.mode === "ranked" ? "牌位" : (record.mode === "matchmaking" ? "匹配" : "自訂");
            title.textContent = `${mode} · ${record.is_draw ? "流局" : `${record.winner_name || "未知"} 胡牌`}`;

            const meta = document.createElement("div");
            meta.className = "profile-home-recent-meta";
            const time = new Date(Number(record.played_at || 0) * 1000).toLocaleDateString("zh-TW");
            const rankText = record.mode === "ranked" ? ` · 段位 ${formatSigned(record.rank_delta || 0)}` : "";
            meta.textContent = `${time} · 排名 ${record.final_rank || "-"} · 分數 ${formatSigned(record.score_delta || 0)} · 金幣 ${formatSigned(record.coin_delta || 0)}${rankText}`;

            item.append(title, meta);
            list.appendChild(item);
        });
    }

    syncLobbyCharacter(characterId) {
        if (window.lobbyCharacterViewer?.setCharacterId) {
            window.lobbyCharacterViewer.setCharacterId(characterId);
        } else {
            window.dispatchEvent(new CustomEvent("lobby-character-select", { detail: { id: characterId } }));
        }
    }

    renderRewardState() {
        const state = this.rewardState || {};
        const daily = state.daily || {};
        const checkin = document.getElementById("btn-daily-checkin");
        const status = document.getElementById("reward-status");
        const countdown = document.getElementById("online-reward-countdown");
        const taskList = document.getElementById("task-list");

        checkin.disabled = Boolean(daily.claimed);
        checkin.textContent = daily.claimed ? "今日已簽到" : `簽到 +${daily.reward || 3}`;
        status.textContent = daily.claimed ? "今日完成" : "可簽到";
        countdown.textContent = formatDuration(state.online_next_seconds ?? state.online?.interval_seconds ?? 1800);

        taskList.innerHTML = "";
        (state.tasks || []).forEach((task) => {
            const item = document.createElement("div");
            item.className = "task-item";
            item.classList.toggle("claimed", Boolean(task.claimed));
            const label = document.createElement("span");
            label.innerHTML = `<strong>${escapeText(task.label)}</strong> +${Number(task.reward || 0)} 金幣`;
            const stateLabel = document.createElement("span");
            stateLabel.textContent = task.claimed ? "完成" : "未完成";
            item.append(label, stateLabel);
            taskList.appendChild(item);
        });
    }

    renderSocialState() {
        const state = this.socialState || {};
        const friends = state.friends || [];
        const incoming = state.incoming || [];
        const outgoing = state.outgoing || [];
        document.getElementById("social-status").textContent = `${friends.length} 位`;
        this.renderFriendRequests(incoming, outgoing);
        this.renderFriendList(friends);
    }

    renderFriendRequests(incoming, outgoing) {
        const container = document.getElementById("friend-requests");
        container.innerHTML = "";

        incoming.forEach((friend) => {
            const item = this.createSocialItem(friend, "邀請你");
            const actions = document.createElement("div");
            actions.className = "social-actions";
            const accept = document.createElement("button");
            accept.type = "button";
            accept.textContent = "接受";
            accept.addEventListener("click", () => this.respondFriendRequest(friend.account_id, true));
            const decline = document.createElement("button");
            decline.type = "button";
            decline.className = "secondary-button";
            decline.textContent = "拒絕";
            decline.addEventListener("click", () => this.respondFriendRequest(friend.account_id, false));
            actions.append(accept, decline);
            item.appendChild(actions);
            container.appendChild(item);
        });

        outgoing.forEach((friend) => {
            const item = this.createSocialItem(friend, "等待回覆");
            container.appendChild(item);
        });
    }

    renderFriendList(friends) {
        const container = document.getElementById("friend-list");
        container.innerHTML = "";
        if (!friends.length) {
            const empty = document.createElement("div");
            empty.className = "social-empty";
            empty.textContent = "尚無好友";
            container.appendChild(empty);
            return;
        }

        friends.forEach((friend) => {
            const item = this.createSocialItem(friend, friend.online ? "在線" : "離線");
            const actions = document.createElement("div");
            actions.className = "social-actions";
            const remove = document.createElement("button");
            remove.type = "button";
            remove.className = "secondary-button";
            remove.textContent = "刪除";
            remove.addEventListener("click", () => this.removeFriend(friend.account_id));
            actions.appendChild(remove);
            item.appendChild(actions);
            container.appendChild(item);
        });
    }

    createSocialItem(friend, metaText) {
        const item = document.createElement("div");
        item.className = "social-item";
        const label = document.createElement("span");
        label.innerHTML = `<strong>${escapeText(friend.display_name || friend.username)}</strong> · ${escapeText(metaText)}`;
        item.appendChild(label);
        return item;
    }

    renderMatchmakingStatus(data) {
        const status = document.getElementById("matchmaking-status");
        const start = document.getElementById("btn-matchmaking");
        const leave = document.getElementById("btn-leave-matchmaking");
        const queued = Boolean(data.queued);
        status.textContent = queued ? `匹配中 ${data.queue_count || 0}/${data.needed || 4}` : `隊伍 ${data.queue_count || 0}/${data.needed || 4}`;
        start.classList.toggle("hidden", queued);
        leave.classList.toggle("hidden", !queued);
    }

    renderRankedStatus(data) {
        const status = document.getElementById("ranked-status");
        const start = document.getElementById("btn-ranked");
        const leave = document.getElementById("btn-leave-ranked");
        const queued = Boolean(data.queued);
        const rankText = data.rank_name ? ` · ${data.rank_name} ${Number(data.rank_points || 0)} 分` : "";
        status.textContent = queued ? `牌位中 ${data.queue_count || 0}/${data.needed || 4}${rankText}` : `隊伍 ${data.queue_count || 0}/${data.needed || 4}`;
        start.classList.toggle("hidden", queued);
        leave.classList.toggle("hidden", !queued);
    }

    renderHistoryResult(data) {
        const profile = data.profile || {};
        const history = data.history || [];
        if (profile.account_id && profile.account_id === this.account?.id) {
            this.selfHistory = history;
            this.renderProfileHome();
        }
        document.getElementById("history-profile-name").textContent = profile.username ? `${profile.username} 的紀錄` : "查無紀錄";
        const list = document.getElementById("history-results");
        list.innerHTML = "";

        if (!history.length) {
            const empty = document.createElement("div");
            empty.className = "history-result-empty";
            empty.textContent = "尚無戰績";
            list.appendChild(empty);
            return;
        }

        history.forEach((record) => {
            const item = document.createElement("article");
            item.className = "history-result-item";

            const title = document.createElement("div");
            title.className = "history-result-title";
            const mode = record.mode === "ranked" ? "牌位" : (record.mode === "matchmaking" ? "匹配" : "自訂");
            const result = record.is_draw ? "整場平手" : `${record.winner_name || "未知"} 整場第一`;
            title.textContent = `${mode} · ${result}`;

            const meta = document.createElement("div");
            meta.className = "history-result-meta";
            const time = new Date(Number(record.played_at || 0) * 1000).toLocaleString("zh-TW");
            const handCount = record.round_detail?.hand_count || record.round_detail?.hands?.length || 0;
            meta.textContent = `${time} · ${handCount} 局 · 排名 ${record.final_rank || "-"} · ${record.duration_seconds || 0}s`;

            const delta = document.createElement("div");
            delta.className = "history-result-delta";
            const coinDelta = Number(record.coin_delta || 0);
            const rankDelta = Number(record.rank_delta || 0);
            const rankText = record.mode === "ranked" ? ` / 段位 ${formatSigned(rankDelta)}` : "";
            delta.textContent = `分數 ${formatSigned(record.score_delta || 0)} / 金幣 ${formatSigned(coinDelta)}${rankText}`;
            delta.classList.toggle("positive", coinDelta > 0);
            delta.classList.toggle("negative", coinDelta < 0);

            const fans = document.createElement("div");
            fans.className = "history-result-fans";
            fans.textContent = `規則 ${record.round_detail?.ruleset_id || "台灣 16 張"} · 整場結算`;

            item.append(title, meta, delta, fans, this.buildHistoryDetails(record));
            list.appendChild(item);
        });
    }

    buildHistoryDetails(record) {
        const details = document.createElement("details");
        details.className = "history-result-details";

        const summary = document.createElement("summary");
        summary.textContent = "明細";
        details.appendChild(summary);

        const body = document.createElement("div");
        body.className = "history-result-detail-body";
        const roundDetail = record.round_detail || {};
        const players = record.players || roundDetail.players || [];
        const hands = roundDetail.hands || [];

        this.appendHistoryDetailRow(
            body,
            "玩家",
            [...players].sort((a, b) => Number(a.final_rank || 99) - Number(b.final_rank || 99)).map((player) => `第${player.final_rank || "-"}名 ${player.name || player.uid}: ${formatSigned(player.cumulative_score ?? player.score_delta ?? 0)}`).join(" / ") || "-"
        );
        this.appendHistoryDetailRow(
            body,
            "整場",
            `${roundDetail.hand_count ?? hands.length} 局 / 規則 ${roundDetail.ruleset_id || "-"} / ${roundDetail.duration_seconds ?? record.duration_seconds ?? 0} 秒`
        );
        this.appendHistoryDetailRow(
            body,
            "各局",
            hands.length ? hands.map((hand) => `第${hand.hand_number || "-"}局 ${hand.result === "DRAW" ? "流局" : `${hand.winner_name || hand.winner_uid || "-"} 胡`} (${Object.values(hand.score_deltas || {}).map(formatSigned).join("/")})`).join(" / ") : "-"
        );

        details.appendChild(body);
        return details;
    }

    appendHistoryDetailRow(container, label, value) {
        const row = document.createElement("div");
        row.className = "history-result-detail-row";
        const labelNode = document.createElement("span");
        labelNode.textContent = label;
        const valueNode = document.createElement("p");
        valueNode.textContent = value;
        row.append(labelNode, valueNode);
        container.appendChild(row);
    }

    formatHistoryEvent(event) {
        const parts = [];
        if (event.seq) {
            parts.push(`#${event.seq}`);
        }
        parts.push(event.type || "EVENT");
        if (event.player_idx !== undefined) {
            parts.push(`座${Number(event.player_idx) + 1}`);
        }
        if (event.action) {
            parts.push(event.action);
        }
        if (event.tile) {
            parts.push(event.tile);
        }
        return parts.join(" ");
    }

    showView(name) {
        document.querySelectorAll(".view, .game-view").forEach((view) => view.classList.add("hidden"));
        document.getElementById(`view-${name}`).classList.remove("hidden");
        document.body.classList.toggle("in-game-view", name === "game");
    }

    handleGameUpdate(state) {
        if (this.isStaleState(state)) {
            return;
        }
        this.applyGameState(state);
    }

    applyGameState(state) {
        if (this.isStaleState(state)) {
            return;
        }
        const previousMe = this.latestState?.players?.find((player) => player.uid === this.account?.id);
        const nextMe = state.players?.find((player) => player.uid === this.account?.id);
        this.syncServerClock(state.server_time);
        this.latestStateSeq = Math.max(this.latestStateSeq, this.stateSeq(state));
        this.latestState = state;
        this.renderer.renderState(state);
        this.gameCharacters.updateState(state, this.account?.id);
        this.gameCharacters.setEnabled(document.body.classList.contains("mode-3d"));
        this.renderer.renderKnowledge(state.knowledge, this.trackerMarks);
        this.updateTurnState(state);
        this.updateAiToggle(state);
        this.updateRoomControls(state);

        if (!previousMe?.guoshui && nextMe?.guoshui) {
            this.renderer.showToast("已過水：暫時不能胡或自摸");
        } else if (previousMe?.guoshui && !nextMe?.guoshui) {
            this.renderer.showToast("已摸打非胡牌，過水解除");
        }

        if (state.decision_deadline) {
            this.startDecisionCountdown(state.decision_deadline, state.decision_timeout_seconds);
        }

        if (!this.myTurn) {
            this.selectedTileIndex = null;
            this.hintRequestKey = "";
            this.renderer.setSelectedTile(null);
            this.renderer.setRecommendedTileIndex(null);
        } else {
            this.requestBeginnerHintIfNeeded();
        }
    }

    stateSeq(state) {
        return Number(state?.state_seq || 0);
    }

    isStaleState(state) {
        const seq = this.stateSeq(state);
        return seq > 0 && seq < this.latestStateSeq;
    }

    syncServerClock(serverTime) {
        const value = Number(serverTime);
        if (Number.isFinite(value) && value > 0) {
            this.serverClockOffset = value - Date.now() / 1000;
        }
    }

    enqueueGameEvent(event) {
        const generation = this.animationGeneration;
        this.animationChain = this.animationChain
            .then(() => {
                if (generation !== this.animationGeneration) {
                    return;
                }
                return this.playGameEvent(event);
            })
            .catch((error) => console.warn("Animation skipped", error));
    }

    cancelGameAnimations() {
        this.animationGeneration += 1;
        this.animationChain = Promise.resolve();
        this.renderer.cancelDiscardAnimations();
    }

    async playGameEvent(event) {
        if (event.type === "DISCARD") {
            const animation = this.renderer.animateDiscard(event);
            this.applyDiscardEventState(event);
            await animation;
        }
    }

    applyDiscardEventState(event) {
        const eventStateSeq = Number(event?.state_seq || 0);
        if (!this.latestState || eventStateSeq <= this.latestStateSeq) {
            return;
        }

        const state = JSON.parse(JSON.stringify(this.latestState));
        const player = state.players?.find((item) => item.uid === event.player_uid);
        if (!player) {
            return;
        }

        const handCountAfter = Number(event.hand_count_after);
        if (Number.isInteger(handCountAfter) && handCountAfter >= 0) {
            if (Array.isArray(player.hand)) {
                while (player.hand.length > handCountAfter) {
                    let tileIndex = Number(event.tile_index);
                    if (
                        !Number.isInteger(tileIndex) ||
                        tileIndex < 0 ||
                        tileIndex >= player.hand.length ||
                        player.hand[tileIndex] !== event.tile
                    ) {
                        tileIndex = player.hand.indexOf(event.tile);
                    }
                    if (tileIndex < 0 || tileIndex >= player.hand.length) {
                        tileIndex = player.hand.length - 1;
                    }
                    player.hand.splice(tileIndex, 1);
                }
            }
            player.hand_count = handCountAfter;
        }

        const discardCountAfter = Number(event.discard_count_after);
        player.discards = Array.isArray(player.discards) ? player.discards : [];
        if (
            Number.isInteger(discardCountAfter) &&
            discardCountAfter >= 0 &&
            player.discards.length < discardCountAfter
        ) {
            player.discards.push(event.tile);
        }

        state.last_discard = { tile: event.tile, from_idx: event.player_idx };
        state.turn_prompt = {
            phase: "PROCESSING",
            actor_uids: [],
            actor_names: [],
            is_recipient: false,
            recipient_actions: [],
        };
        state.state_seq = eventStateSeq;
        this.latestStateSeq = eventStateSeq;
        this.latestState = state;
        this.renderer.renderState(state);
        this.gameCharacters.updateState(state, this.account?.id);

        if (event.player_uid === this.account?.id) {
            this.myTurn = false;
            this.selectedTileIndex = null;
            this.hintRequestKey = "";
            this.renderer.setSelectedTile(null);
            this.renderer.setRecommendedTileIndex(null);
            this.renderer.setDiscardEnabled(false);
        }
    }

    updateTurnState(state) {
        const me = state.players.find((player) => player.uid === this.account?.id);
        this.myTurn = Boolean(me && state.current_turn_idx === me.idx && state.state === "PLAYER_TURN");
        this.renderer.setDiscardEnabled(this.myTurn);
    }

    updateAiToggle(state) {
        const me = state.players.find((player) => player.uid === this.account?.id);
        if (me) {
            this.aiEnabled = Boolean(me.ai_enabled);
            this.autoPlayEnabled = Boolean(me.auto_play_enabled);
            this.renderAiToggle();
        }
    }

    renderAiToggle() {
        const button = document.getElementById("ai-toggle");
        button.textContent = this.autoPlayEnabled ? "AI AUTO" : (this.aiEnabled ? "AI ON" : "AI OFF");
        button.classList.toggle("active", this.aiEnabled || this.autoPlayEnabled);
    }

    updateRoomControls(state) {
        const canDissolve = state?.room_mode === "custom" && state?.host_uid === this.account?.id;
        document.getElementById("dissolve-room-button").classList.toggle("hidden", !canDissolve);
        document.getElementById("lobby-dissolve-room-button").classList.toggle("hidden", !canDissolve);
    }

    renderBeginnerToggle() {
        const button = document.getElementById("beginner-toggle");
        button.textContent = this.beginnerEnabled ? "新手 ON" : "新手 OFF";
        button.classList.toggle("active", this.beginnerEnabled);
    }

    renderPanelToggle() {
        const panel = document.getElementById("knowledge-panel");
        const button = document.getElementById("knowledge-toggle");
        document.body.classList.toggle("info-open", this.infoPanelOpen);
        panel.classList.toggle("hidden", !this.infoPanelOpen);
        button.classList.toggle("active", this.infoPanelOpen);
    }

    renderChatToggle() {
        const panel = document.getElementById("chat-panel");
        const button = document.getElementById("chat-toggle");
        document.body.classList.toggle("chat-open", this.chatPanelOpen);
        panel.classList.toggle("hidden", !this.chatPanelOpen);
        button.classList.toggle("active", this.chatPanelOpen);
    }

    selectTile(index) {
        if (!this.myTurn) {
            this.renderer.showToast("還沒輪到你");
            return;
        }
        this.selectedTileIndex = index;
        this.renderer.setSelectedTile(index);
        this.renderer.setDiscardEnabled(true);
    }

    discardTile(index = this.selectedTileIndex) {
        if (!this.myTurn || index === null || index === undefined) {
            return;
        }
        const actionId = `${Date.now()}-${++this.discardActionSeq}`;
        this.pendingLocalDiscardIds.add(actionId);
        this.socket.emit("action_discard", {
            room_id: this.roomId,
            tile_index: index,
            client_action_id: actionId,
        });
        this.renderOptimisticDiscard(index, actionId);
        setTimeout(() => this.pendingLocalDiscardIds.delete(actionId), 10000);
        this.selectedTileIndex = null;
        this.hintRequestKey = "";
        this.renderer.setSelectedTile(null);
        this.renderer.setRecommendedTileIndex(null);
        this.renderer.setDiscardEnabled(false);
    }

    renderOptimisticDiscard(index, actionId) {
        if (!this.latestState) {
            return;
        }
        const tileIndex = Number(index);
        const state = JSON.parse(JSON.stringify(this.latestState));
        const me = state.players.find((player) => player.uid === this.account?.id);
        if (!me?.hand || tileIndex < 0 || tileIndex >= me.hand.length) {
            return;
        }

        const tile = me.hand[tileIndex];
        void this.renderer.animateDiscard({
            type: "DISCARD",
            player_uid: me.uid,
            player_idx: me.idx,
            tile,
            tile_index: tileIndex,
            client_action_id: actionId,
        });
        me.hand.splice(tileIndex, 1);
        me.hand_count = me.hand.length;
        me.discards = Array.isArray(me.discards) ? me.discards : [];
        me.discards.push(tile);
        state.last_discard = { tile, from_idx: me.idx };
        state.turn_prompt = {
            phase: "PROCESSING",
            actor_uids: [],
            actor_names: [],
            is_recipient: false,
            recipient_actions: [],
        };
        this.latestState = state;
        this.renderer.renderState(state);
        this.gameCharacters.updateState(state, this.account?.id);
        this.myTurn = false;
    }

    sendAction(action) {
        let clientActionId;
        if (action.type === "TING" && Number.isInteger(action.tile_index)) {
            clientActionId = `${Date.now()}-${++this.discardActionSeq}`;
            this.pendingLocalDiscardIds.add(clientActionId);
        }
        this.socket.emit("action_reply", {
            type: action.type,
            tile: action.tile,
            tiles: action.tiles,
            tile_index: action.tile_index,
            client_action_id: clientActionId,
        });
        if (clientActionId) {
            this.renderOptimisticDiscard(action.tile_index, clientActionId);
            setTimeout(() => this.pendingLocalDiscardIds.delete(clientActionId), 10000);
            this.selectedTileIndex = null;
            this.hintRequestKey = "";
            this.renderer.setSelectedTile(null);
            this.renderer.setRecommendedTileIndex(null);
            this.renderer.setDiscardEnabled(false);
        }
        this.renderer.clearActionButtons();
    }

    requestNextRound() {
        this.socket.emit("request_next_round", {});
    }

    requestRematch() {
        this.socket.emit("request_rematch", {});
    }

    leaveRoom(queueMode = null) {
        if (!this.roomId) {
            this.finishLeavingRoom("已回到首頁", queueMode);
            return;
        }

        const roomState = this.latestState?.room_state;
        const activeMatch = roomState === "GAME" || roomState === "HAND_ENDED";
        if (activeMatch && !window.confirm("現在退出會判本場第4名，並解散牌桌。確定退出嗎？")) {
            return;
        }

        this.pendingQueueAfterLeave = queueMode;
        this.socket.emit("leave_room", {});
    }

    dissolveRoom() {
        if (!this.roomId) {
            return;
        }
        const roomState = this.latestState?.room_state;
        const suffix = roomState === "GAME" || roomState === "HAND_ENDED"
            ? "你會被判本場第4名。"
            : "";
        if (!window.confirm(`確定解散房間嗎？${suffix}`)) {
            return;
        }
        this.socket.emit("dissolve_room", {});
    }

    finishLeavingRoom(message, queueMode = this.pendingQueueAfterLeave) {
        this.cancelGameAnimations();
        this.pendingQueueAfterLeave = null;
        this.roomId = null;
        this.latestState = null;
        this.latestStateSeq = 0;
        this.selectedTileIndex = null;
        this.myTurn = false;
        this.aiEnabled = false;
        this.autoPlayEnabled = false;
        this.pendingLocalDiscardIds.clear();
        this.renderer.hideEndGame();
        this.renderer.clearActionButtons();
        this.renderer.setSelectedTile(null);
        this.renderer.setRecommendedTileIndex(null);
        this.clearDecisionCountdown();
        this.updateRoomControls({});
        this.renderAiToggle();
        this.showView("home");
        this.renderer.showToast(message);
        this.requestHistory(true);

        if (queueMode === "ranked") {
            this.joinRanked();
        } else if (queueMode === "matchmaking") {
            this.joinMatchmaking();
        }
    }

    toggleAi() {
        this.aiEnabled = !this.aiEnabled;
        this.renderAiToggle();
        this.socket.emit("set_ai_enabled", { enabled: this.aiEnabled });
    }

    toggleBeginnerMode() {
        this.beginnerEnabled = !this.beginnerEnabled;
        window.localStorage.setItem("tw_mahjong_beginner", this.beginnerEnabled ? "1" : "0");
        this.renderBeginnerToggle();
        if (this.beginnerEnabled) {
            this.requestBeginnerHintIfNeeded(true);
        } else {
            this.hintRequestKey = "";
            this.renderer.setRecommendedTileIndex(null);
        }
    }

    toggleKnowledgePanel() {
        this.infoPanelOpen = !this.infoPanelOpen;
        window.localStorage.setItem("tw_mahjong_info_open", this.infoPanelOpen ? "1" : "0");
        this.renderPanelToggle();
    }

    toggleChatPanel() {
        this.chatPanelOpen = !this.chatPanelOpen;
        window.localStorage.setItem("tw_mahjong_chat_open", this.chatPanelOpen ? "1" : "0");
        this.renderChatToggle();
    }

    sendChatText() {
        const input = document.getElementById("chat-input");
        const text = input.value.trim();
        if (!text) {
            return;
        }
        this.socket.emit("chat_send", { kind: "text", text });
        input.value = "";
    }

    sendSticker(stickerId) {
        this.socket.emit("chat_send", { kind: "sticker", sticker_id: stickerId });
    }

    sendChatImage(event) {
        const input = event.currentTarget;
        const file = input.files?.[0];
        input.value = "";
        if (!file) {
            return;
        }
        if (!["image/png", "image/jpeg", "image/gif", "image/webp"].includes(file.type)) {
            this.renderer.showToast("只支援 PNG、JPEG、GIF、WebP");
            return;
        }
        if (file.size > CHAT_MAX_IMAGE_BYTES) {
            this.renderer.showToast(`圖片需 ${CHAT_MAX_IMAGE_MB} MB 以下`);
            return;
        }

        const reader = new FileReader();
        reader.addEventListener("load", () => {
            this.socket.emit("chat_send", {
                kind: "image",
                image: {
                    data_url: reader.result,
                    mime_type: file.type,
                    size: file.size,
                    name: file.name,
                },
            });
        });
        reader.addEventListener("error", () => this.renderer.showToast("圖片讀取失敗"));
        reader.readAsDataURL(file);
    }

    toggleViewMode() {
        const body = document.body;
        const is3d = body.classList.toggle("mode-3d");
        body.classList.toggle("mode-2d", !is3d);
        document.getElementById("view-mode-toggle").textContent = is3d ? "2D" : "3D";
        this.gameCharacters.setEnabled(is3d);
    }

    requestBeginnerHintIfNeeded(force = false) {
        if (!this.beginnerEnabled || !this.myTurn || !this.latestState) {
            return;
        }
        const me = this.latestState.players.find((player) => player.uid === this.account?.id);
        if (!me?.hand?.length) {
            return;
        }
        const key = `${me.hand.join("|")}::${this.latestState.wall_remaining_count}`;
        if (!force && this.hintRequestKey === key) {
            return;
        }
        this.hintRequestKey = key;
        this.socket.emit("ask_hint", {});
    }

    cycleTrackerMark(tile) {
        const current = this.trackerMarks[tile] || "無";
        const next = MARK_VALUES[(MARK_VALUES.indexOf(current) + 1) % MARK_VALUES.length];
        if (next === "無") {
            delete this.trackerMarks[tile];
        } else {
            this.trackerMarks[tile] = next;
        }
        this.saveTrackerMarks();
        this.renderer.renderKnowledge(this.latestState?.knowledge, this.trackerMarks);
    }

    loadTrackerMarks() {
        try {
            this.trackerMarks = JSON.parse(window.localStorage.getItem(this.trackerMarkKey()) || "{}");
        } catch (_error) {
            this.trackerMarks = {};
        }
    }

    saveTrackerMarks() {
        window.localStorage.setItem(this.trackerMarkKey(), JSON.stringify(this.trackerMarks));
    }

    trackerMarkKey() {
        return `tw_mahjong_tracker_marks_${this.account?.id || "guest"}`;
    }

    startDecisionCountdown(deadline, fallbackSeconds = 10) {
        if (!deadline) {
            this.renderer.renderDecisionTimer(fallbackSeconds);
            return;
        }
        clearInterval(this.countdownTimer);
        const update = () => {
            const remaining = Number(deadline) - (Date.now() / 1000 + this.serverClockOffset);
            this.renderer.renderDecisionTimer(remaining);
            if (remaining <= 0) {
                clearInterval(this.countdownTimer);
                this.countdownTimer = null;
            }
        };
        update();
        this.countdownTimer = setInterval(update, 250);
    }

    clearDecisionCountdown() {
        clearInterval(this.countdownTimer);
        this.countdownTimer = null;
        this.renderer.clearDecisionTimer();
    }
}

function formatSigned(value) {
    const number = Number(value || 0);
    return number > 0 ? `+${number}` : String(number);
}

function formatDuration(seconds) {
    const safeSeconds = Math.max(0, Math.ceil(Number(seconds || 0)));
    const minutes = Math.floor(safeSeconds / 60);
    const rest = safeSeconds % 60;
    return `${minutes}:${String(rest).padStart(2, "0")}`;
}

function escapeText(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}
