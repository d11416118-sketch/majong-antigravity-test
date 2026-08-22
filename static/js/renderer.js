import { getTheme, playerThemeClass } from "./themes.js";

const TILE_LABELS = {
    "1m": "1萬", "2m": "2萬", "3m": "3萬", "4m": "4萬", "5m": "5萬", "6m": "6萬", "7m": "7萬", "8m": "8萬", "9m": "9萬",
    "1p": "1筒", "2p": "2筒", "3p": "3筒", "4p": "4筒", "5p": "5筒", "6p": "6筒", "7p": "7筒", "8p": "8筒", "9p": "9筒",
    "1s": "1條", "2s": "2條", "3s": "3條", "4s": "4條", "5s": "5條", "6s": "6條", "7s": "7條", "8s": "8條", "9s": "9條",
    "1z": "東", "2z": "南", "3z": "西", "4z": "北", "5z": "中", "6z": "發", "7z": "白",
    "1f": "春", "2f": "夏", "3f": "秋", "4f": "冬", "5f": "梅", "6f": "蘭", "7f": "竹", "8f": "菊",
    "BACK": ""
};

const ACTION_LABELS = {
    HU: "胡",
    CHI: "吃",
    PON: "碰",
    KANG: "槓",
    ANKANG: "暗槓",
    BUKANG: "補槓",
    TING: "聽牌",
    PASS: "過"
};

const POSITIONS = ["bottom", "right", "top", "left"];

export class Renderer {
    constructor({
        onTileClick,
        onAction,
        onDiscard,
        onTrackerMark,
        onNextRound,
        onRematch,
        onLeaveRoom,
        onSticker,
    }) {
        this.onTileClick = onTileClick;
        this.onAction = onAction;
        this.onDiscard = onDiscard;
        this.onTrackerMark = onTrackerMark;
        this.onNextRound = onNextRound;
        this.onRematch = onRematch;
        this.onLeaveRoom = onLeaveRoom;
        this.onSticker = onSticker;
        this.selectedTileIndex = null;
        this.recommendedTileIndex = null;
        this.myUid = null;
        this.myIdx = -1;

        this.discardButton = document.getElementById("discard-button");
        this.actionBar = document.getElementById("action-bar");
        this.toast = document.getElementById("toast");
        this.endOverlay = document.getElementById("end-game-overlay");
        this.countdown = document.getElementById("decision-countdown");
        this.activeDiscardAnimations = new Set();
        this.historyList = document.getElementById("history-list");
        this.trackerGrid = document.getElementById("tile-tracker");
        this.chatList = document.getElementById("chat-list");
        this.turnStatusBanner = document.getElementById("turn-status-banner");
        this.turnStatusTitle = document.getElementById("turn-status-title");
        this.turnStatusDetail = document.getElementById("turn-status-detail");

        this.discardButton.addEventListener("click", () => this.onDiscard());
        document.querySelectorAll("#sticker-row button").forEach((button) => {
            button.addEventListener("click", () => this.onSticker(button.dataset.stickerId));
        });
    }

    setIdentity(uid) {
        this.myUid = uid;
    }

    setSelectedTile(index) {
        this.selectedTileIndex = index;
        document.querySelectorAll(".tile.selectable").forEach((tile) => {
            tile.classList.toggle("selected", Number(tile.dataset.index) === index);
        });
        this.discardButton.disabled = index === null;
    }

    setRecommendedTileIndex(index) {
        this.recommendedTileIndex = Number.isInteger(index) ? index : null;
        document.querySelectorAll(".tile.selectable").forEach((tile) => {
            tile.classList.toggle("recommended", Number(tile.dataset.index) === this.recommendedTileIndex);
        });
    }

    setDiscardEnabled(enabled) {
        this.discardButton.disabled = !enabled || this.selectedTileIndex === null;
    }

    renderLobby(data) {
        document.getElementById("lobby-room-id").textContent = data.room_id;
        document.getElementById("lobby-status").textContent = `${data.count}/${data.total}`;
        const settings = document.getElementById("lobby-room-settings");
        if (settings) {
            const mode = data.mode === "ranked" ? "牌位模式" : (data.mode === "matchmaking" ? "匹配模式" : "自訂模式");
            const scoring = data.track_stats ? `計戰績 / 底注 ${data.base_stake}` : "休閒房";
            const ruleSummary = data.rules
                ? `${data.rules.allow_multi_hu ? "一炮多響" : "攔胡"} / 嚴格過水 / 宣告聽牌 / 正花制`
                : "攔胡 / 嚴格過水 / 宣告聽牌 / 正花制";
            settings.textContent = `${mode} · ${data.ruleset_name || "台灣 16 張"} · ${ruleSummary} · ${scoring}`;
            const special = data.rules?.special_fans || {};
            settings.title = `合法牌型即可胡；宣告聽牌 ${special.declared_ting ?? 1} 台、地聽 ${special.di_ting ?? 4} 台；八仙過海、七搶一各 ${special.flower_win ?? 8} 台；搶槓、槓上、海底、河底各 ${special.qiang_gang ?? 1} 台；天胡 ${special.tian_hu ?? 24}、地胡 ${special.di_hu ?? 16}、人胡 ${special.ren_hu ?? 8} 台`;
        }

        const list = document.getElementById("lobby-player-list");
        list.innerHTML = "";
        data.players.forEach((player, index) => {
            const item = document.createElement("div");
            item.className = "lobby-item";
            item.classList.add(playerThemeClass(player.theme_id));
            const theme = getTheme(player.theme_id);
            item.innerHTML = `
                <span>${index + 1}</span>
                <strong>${escapeHtml(player.name)}</strong>
                <small class="player-theme-label">${escapeHtml(theme.publicLabel)}</small>
                ${statusBadges(player)}
            `;
            list.appendChild(item);
        });
    }

    renderState(state) {
        this.lastState = state;
        const me = state.players.find((player) => player.uid === this.myUid);
        this.myIdx = me ? me.idx : 0;

        document.getElementById("game-room-id").textContent = state.room_id || "------";
        document.getElementById("wall-count").textContent = state.wall_remaining_count;
        this.renderMatchProgress(state);

        const turnPlayer = state.players.find((player) => player.idx === state.current_turn_idx);
        const promptNames = state.turn_prompt?.actor_names || [];
        document.getElementById("turn-indicator").textContent = state.turn_prompt?.phase === "RESPONSE"
            ? (promptNames.length ? `${promptNames.join("、")} 回應` : "判定中")
            : (turnPlayer ? turnPlayer.name : "");
        document.getElementById("last-discard").textContent = state.last_discard ? tileLabel(state.last_discard.tile) : "";
        this.renderTurnPrompt(state);

        const actorUids = new Set(state.turn_prompt?.actor_uids || []);
        state.players.forEach((player) => {
            const relative = (player.idx - this.myIdx + 4) % 4;
            const seat = document.querySelector(`.seat-${POSITIONS[relative]}`);
            if (!seat) return;
            const isDiscardTurn = state.state === "PLAYER_TURN" && state.current_turn_idx === player.idx;
            const isResponseTurn = state.state === "WAIT_RESPONSE" && actorUids.has(player.uid);
            this.renderSeat(seat, player, isDiscardTurn, isResponseTurn, player.uid === this.myUid);
        });
        this.setRecommendedTileIndex(this.recommendedTileIndex);
    }

    renderTurnPrompt(state) {
        if (!this.turnStatusBanner || !this.turnStatusTitle || !this.turnStatusDetail) return;

        const prompt = state.turn_prompt || {};
        const names = prompt.actor_names || [];
        const actionNames = (prompt.recipient_actions || []).map((type) => ACTION_LABELS[type] || type);
        this.turnStatusBanner.dataset.phase = String(prompt.phase || "idle").toLowerCase();
        this.turnStatusBanner.classList.toggle("mine", Boolean(prompt.is_recipient));

        if (prompt.phase === "DISCARD") {
            this.turnStatusTitle.textContent = prompt.is_recipient ? "輪到你出牌" : `輪到 ${names[0] || "玩家"} 出牌`;
            this.turnStatusDetail.textContent = prompt.is_recipient
                ? "請選擇一張手牌打出"
                : `正在等待 ${names[0] || "玩家"} 完成出牌`;
            return;
        }

        if (prompt.phase === "RESPONSE") {
            if (prompt.is_recipient) {
                this.turnStatusTitle.textContent = "輪到你回應";
                this.turnStatusDetail.textContent = actionNames.length
                    ? `可選：${actionNames.join("／")}／過`
                    : "請選擇可用動作或按過";
            } else if (names.length) {
                this.turnStatusTitle.textContent = `等待 ${names.join("、")} 回應`;
                this.turnStatusDetail.textContent = "正在處理胡／碰／槓／吃或過";
            } else {
                this.turnStatusTitle.textContent = "正在結算回應";
                this.turnStatusDetail.textContent = "伺服器正在判定動作優先順序";
            }
            return;
        }

        if (prompt.phase === "END") {
            this.turnStatusTitle.textContent = "本局已結束";
            this.turnStatusDetail.textContent = "請查看結算結果";
            return;
        }

        if (prompt.phase === "PROCESSING") {
            this.turnStatusTitle.textContent = "出牌已送出";
            this.turnStatusDetail.textContent = "等待伺服器確認下一位操作玩家";
            return;
        }

        this.turnStatusTitle.textContent = "等待牌局開始";
        this.turnStatusDetail.textContent = "系統會在這裡顯示目前由誰操作";
    }

    renderMatchProgress(state) {
        const windNames = ["東", "南", "西", "北"];
        const wind = windNames[Number(state.round_wind || 0)] || `第${Number(state.round_wind || 0) + 1}圈`;
        const handLabel = `${wind}${Number(state.dealer_idx || 0) + 1}局`;
        const lianLabel = Number(state.lian_zhuang || 0) > 0 ? ` · 連${state.lian_zhuang}` : "";
        const progress = document.getElementById("match-progress");
        if (progress) progress.textContent = `${handLabel}${lianLabel}`;

        const scoreboard = document.getElementById("match-scoreboard");
        if (!scoreboard) return;
        const scores = state.match?.cumulative_scores || {};
        const rankedPlayers = [...state.players].sort((left, right) => {
            const scoreDifference = Number(scores[right.uid] ?? right.score ?? 0) - Number(scores[left.uid] ?? left.score ?? 0);
            return scoreDifference || Number(left.idx || 0) - Number(right.idx || 0);
        });
        const completedHands = Number(state.match?.completed_hand_count || 0);
        scoreboard.innerHTML = `
            <div class="match-scoreboard-title">
                <strong>目前排名</strong>
                <small>${handLabel}${lianLabel} · 已完成 ${completedHands} 局</small>
            </div>
            <div class="match-scoreboard-grid">
                ${rankedPlayers.map((player, rankIndex) => `
                    <span class="${player.idx === state.dealer_idx ? "dealer" : ""} ${playerThemeClass(player.theme_id)}">
                        <i>${rankIndex + 1}</i>
                        <b>${player.idx === state.dealer_idx ? "莊 " : ""}${escapeHtml(player.name)}</b>
                        <em>${formatSignedScore(scores[player.uid] ?? player.score ?? 0)}</em>
                    </span>
                `).join("")}
            </div>
        `;
    }

    renderKnowledge(knowledge, marks = {}) {
        if (!knowledge) return;
        this.renderHistory(knowledge);
        this.renderTileTracker(knowledge.tile_tracker || [], marks);
    }

    renderHistory(knowledge) {
        if (!this.historyList) return;
        const namesByIdx = new Map((knowledge.players || []).map((player) => [player.idx, player.name]));
        const rows = (knowledge.history || []).slice(-48).reverse();
        this.historyList.innerHTML = "";

        if (!rows.length) {
            const empty = document.createElement("div");
            empty.className = "history-empty";
            empty.textContent = "本局尚無紀錄";
            this.historyList.appendChild(empty);
            return;
        }

        rows.forEach((event) => {
            const item = document.createElement("div");
            item.className = "history-item";
            item.textContent = formatHistory(event, namesByIdx);
            this.historyList.appendChild(item);
        });
    }

    renderTileTracker(tracker, marks) {
        if (!this.trackerGrid) return;
        this.trackerGrid.innerHTML = "";

        tracker.forEach((item) => {
            const row = document.createElement("div");
            row.className = "tracker-tile";
            row.classList.toggle("exhausted", item.unknown_count === 0);

            const label = document.createElement("span");
            label.className = "tracker-label";
            label.textContent = tileLabel(item.tile);
            row.appendChild(label);

            const pips = document.createElement("div");
            pips.className = "tracker-pips";
            for (let index = 0; index < item.total; index += 1) {
                const pip = document.createElement("span");
                pip.className = index < item.known_count ? "known" : "unknown";
                pips.appendChild(pip);
            }
            row.appendChild(pips);

            const mark = document.createElement("button");
            mark.type = "button";
            mark.className = "tracker-mark";
            mark.disabled = item.unknown_count === 0;
            mark.textContent = marks[item.tile] || "無";
            mark.title = "標記猜測";
            mark.addEventListener("click", () => this.onTrackerMark(item.tile));
            row.appendChild(mark);

            this.trackerGrid.appendChild(row);
        });
    }

    renderDecisionTimer(seconds) {
        if (!this.countdown) return;
        this.countdown.textContent = `${Math.max(0, Math.ceil(seconds))}s`;
        this.countdown.classList.toggle("urgent", seconds <= 3);
    }

    clearDecisionTimer() {
        if (!this.countdown) return;
        this.countdown.textContent = "--";
        this.countdown.classList.remove("urgent");
    }

    async animateDiscard(event) {
        const seat = this.seatForPlayer(event.player_idx);
        if (!seat) return;

        const source = this.findDiscardSource(seat, event);
        const target = seat.querySelector(".discard-row");
        if (!source || !target) return;

        const sourceRect = source.getBoundingClientRect();
        const targetPoint = this.nextDiscardPoint(target);
        if (!sourceRect.width || !sourceRect.height) return;

        const floating = this.createTile(event.tile, {});
        floating.classList.add("floating-tile");
        floating.disabled = true;
        Object.assign(floating.style, {
            left: `${sourceRect.left}px`,
            top: `${sourceRect.top}px`,
            width: `${sourceRect.width}px`,
            height: `${sourceRect.height}px`,
        });

        document.body.appendChild(floating);
        source.classList.add("animating-source");

        const startX = sourceRect.left + sourceRect.width / 2;
        const startY = sourceRect.top + sourceRect.height / 2;
        const dx = targetPoint.x - startX;
        const dy = targetPoint.y - startY;
        const endScale = Math.max(0.62, Math.min(0.82, 28 / sourceRect.width));

        try {
            const animation = floating.animate(
                [
                    { transform: "translate(0, 0) scale(1)", opacity: 1 },
                    { transform: `translate(${dx * 0.38}px, ${dy * 0.38 - 34}px) scale(1.06)`, opacity: 1, offset: 0.42 },
                    { transform: `translate(${dx}px, ${dy}px) rotate(7deg) scale(${endScale})`, opacity: 0.96 },
                ],
                { duration: 420, easing: "cubic-bezier(.2,.8,.2,1)" }
            );
            this.activeDiscardAnimations.add(animation);
            try {
                await animation.finished;
            } finally {
                this.activeDiscardAnimations.delete(animation);
            }
        } catch (_error) {
            // Browser can cancel animations during reload or tab switch.
        } finally {
            source.classList.remove("animating-source");
            floating.remove();
        }
    }

    cancelDiscardAnimations() {
        this.activeDiscardAnimations.forEach((animation) => animation.cancel());
        this.activeDiscardAnimations.clear();
        document.querySelectorAll(".floating-tile").forEach((tile) => tile.remove());
        document.querySelectorAll(".animating-source").forEach((tile) => tile.classList.remove("animating-source"));
    }

    seatForPlayer(playerIdx) {
        const relative = (playerIdx - this.myIdx + 4) % 4;
        return document.querySelector(`.seat-${POSITIONS[relative]}`);
    }

    findDiscardSource(seat, event) {
        const exact = seat.querySelector(`.hand-row .tile[data-index="${event.tile_index}"]`);
        if (exact) return exact;

        const handTiles = [...seat.querySelectorAll(".hand-row .tile")];
        if (handTiles.length > 0) {
            return handTiles[Math.max(0, handTiles.length - 1)];
        }

        return seat.querySelector(".hand-count");
    }

    nextDiscardPoint(target) {
        const rect = target.getBoundingClientRect();
        const count = target.querySelectorAll(".tile").length;
        const tileW = 28;
        const tileH = 38;
        const gap = 3;
        const pad = 4;
        const columns = Math.max(1, Math.floor((rect.width - pad * 2 + gap) / (tileW + gap)));
        const col = count % columns;
        const row = Math.floor(count / columns);

        return {
            x: rect.left + pad + col * (tileW + gap) + tileW / 2,
            y: rect.top + pad + row * (tileH + gap) + tileH / 2,
        };
    }

    renderSeat(seat, player, isDiscardTurn, isResponseTurn, isMe) {
        seat.classList.toggle("active-turn", isDiscardTurn);
        seat.classList.toggle("active-response", isResponseTurn);
        seat.innerHTML = "";

        const info = document.createElement("div");
        info.className = "player-info";
        info.classList.add(playerThemeClass(player.theme_id));
        const theme = getTheme(player.theme_id);
        info.innerHTML = `
            <strong>${escapeHtml(player.name)}</strong>
            <i class="player-theme-chip">${escapeHtml(theme.publicLabel)}</i>
            <span>${player.score || 0}</span>
            ${isDiscardTurn ? `<em class="operation-badge">${isMe ? "請出牌" : "出牌中"}</em>` : ""}
            ${isResponseTurn ? `<em class="operation-badge response">${isMe ? "請回應" : "回應中"}</em>` : ""}
            ${statusBadges(player)}
        `;
        seat.appendChild(info);

        const hand = document.createElement("div");
        hand.className = "hand-row";
        if (isMe && player.hand) {
            player.hand.forEach((tile, index) => {
                hand.appendChild(this.createTile(tile, { interactive: true, index }));
            });
        } else {
            const sideSeat = seat.classList.contains("seat-left") || seat.classList.contains("seat-right");
            const previewLimit = sideSeat ? 4 : 8;
            const previewCount = Math.min(player.hand_count || 0, previewLimit);
            for (let index = 0; index < previewCount; index += 1) {
                hand.appendChild(this.createTile("BACK", { hidden: true }));
            }
            const counter = document.createElement("span");
            counter.className = "hand-count";
            counter.textContent = player.hand_count || 0;
            hand.appendChild(counter);
        }
        seat.appendChild(hand);

        const melds = document.createElement("div");
        melds.className = "meld-row";
        (player.melds || []).forEach((meld) => melds.appendChild(this.createMeld(meld)));
        seat.appendChild(melds);

        const flowers = document.createElement("div");
        flowers.className = "flower-row";
        (player.flowers || []).forEach((tile) => flowers.appendChild(this.createTile(tile, { small: true })));
        seat.appendChild(flowers);

        const discards = document.createElement("div");
        discards.className = "discard-row";
        (player.discards || player.graveyard || []).forEach((tile) => discards.appendChild(this.createTile(tile, { small: true })));
        seat.appendChild(discards);
    }

    createMeld(meld) {
        const group = document.createElement("div");
        group.className = "meld-group";
        const tiles = meld.tiles || [meld.tile, meld.tile, meld.tile];
        tiles.forEach((tile) => group.appendChild(this.createTile(tile, { small: true, hidden: tile === "BACK" })));
        return group;
    }

    createTile(tile, options = {}) {
        const el = document.createElement("button");
        el.type = "button";
        const suitChar = tile === "BACK" ? "back-face" : tile.slice(-1);
        el.className = `tile tile-${suitChar}`;
        el.dataset.tile = tile;
        el.textContent = tileLabel(tile);

        if (options.hidden) {
            el.classList.add("tile-back");
        }
        if (options.small) {
            el.classList.add("tile-small");
        }
        if (options.interactive) {
            el.classList.add("selectable");
            el.dataset.index = options.index;
            el.addEventListener("click", () => this.onTileClick(options.index));
            el.addEventListener("dblclick", () => this.onDiscard(options.index));
        } else {
            el.disabled = true;
        }
        if (options.index === this.selectedTileIndex) {
            el.classList.add("selected");
        }
        if (options.index === this.recommendedTileIndex) {
            el.classList.add("recommended");
        }

        return el;
    }

    showActionButtons(actions) {
        this.actionBar.innerHTML = "";
        actions.forEach((action) => {
            const button = document.createElement("button");
            button.type = "button";
            button.className = "action-button";
            let detail = action.type === "CHI" && action.tiles ? ` ${action.tiles.map(tileLabel).join(" ")}` : "";
            if (action.type === "TING") {
                const waits = (action.ting_tiles || []).map(tileLabel).join("、");
                detail = `（打 ${tileLabel(action.tile)}，聽 ${waits}）`;
            }
            button.textContent = `${ACTION_LABELS[action.type] || action.type}${detail}`;
            button.addEventListener("click", () => this.onAction(action));
            this.actionBar.appendChild(button);
        });

        const passButton = document.createElement("button");
        passButton.type = "button";
        passButton.className = "action-button secondary";
        passButton.textContent = ACTION_LABELS.PASS;
        passButton.addEventListener("click", () => this.onAction({ type: "PASS" }));
        this.actionBar.appendChild(passButton);
    }

    clearActionButtons() {
        this.actionBar.innerHTML = "";
    }

    hideEndGame() {
        this.endOverlay.classList.add("hidden");
    }

    showToast(message) {
        this.toast.textContent = message;
        this.toast.classList.remove("hidden");
        clearTimeout(this.toastTimer);
        this.toastTimer = setTimeout(() => this.toast.classList.add("hidden"), 2600);
    }

    renderEndGame(data) {
        this.endOverlay.classList.remove("hidden");
        const breakdown = data.score_breakdown?.breakdown || [];
        const rows = breakdown.map((item) => `<li><span>${escapeHtml(item.name)}</span><strong>+${item.value}</strong></li>`).join("");
        const resultTitle = data.winner_uid ? `${escapeHtml(data.winner_name)} 胡牌` : "流局";
        const winTile = data.winning_tile ? `<div class="win-tile">${tileLabel(data.winning_tile)}</div>` : "";

        const playerNames = new Map((this.lastState?.players || []).map((player) => [player.uid, player.name]));
        const scoreDeltas = data.score_deltas || {};
        const cumulativeScores = data.cumulative_scores || {};
        const scoreRows = Object.keys(cumulativeScores).map((uid) => `
            <li class="hand-score-row">
                <span>${escapeHtml(playerNames.get(uid) || uid)}</span>
                <strong>${formatSignedScore(scoreDeltas[uid] || 0)} / 累計 ${formatSignedScore(cumulativeScores[uid] || 0)}</strong>
            </li>
        `).join("");
        const matchEnded = Boolean(data.match_ended);
        const nextRound = matchEnded ? "" : `
            <div id="next-round-status" class="next-round-status">等待四位玩家準備下一局</div>
            <button id="next-round-button" type="button">準備下一局</button>
        `;

        this.endOverlay.innerHTML = `
            <div class="end-panel">
                <h2>${resultTitle}</h2>
                ${winTile}
                <p>${escapeHtml(data.score_breakdown?.dealer_message || data.dealer_message || "")}</p>
                <h3>${data.score_breakdown?.fan_total || 0} 台 + ${data.score_breakdown?.base || 0} 底</h3>
                ${data.score_breakdown?.dealer_liability_bonus ? `<p>莊家付款另加 ${data.score_breakdown.dealer_liability_bonus} 分</p>` : ""}
                <ul>${rows}</ul>
                <h3 class="settlement-title">本局 / 整場分數</h3>
                <ul class="hand-score-list">${scoreRows}</ul>
                ${matchEnded ? '<div class="next-round-status">東風場已完成，正在結算整場名次</div>' : ""}
                <div class="end-actions">
                    ${nextRound}
                    <button id="leave-room-from-hand" class="danger-room-button" type="button">退出牌桌</button>
                    <button id="close-end-overlay" type="button">關閉</button>
                </div>
            </div>
        `;
        document.getElementById("next-round-button")?.addEventListener("click", () => {
            const button = document.getElementById("next-round-button");
            button.disabled = true;
            button.textContent = "等待其他玩家";
            this.onNextRound();
        });
        document.getElementById("close-end-overlay").addEventListener("click", () => {
            this.endOverlay.classList.add("hidden");
        });
        document.getElementById("leave-room-from-hand").addEventListener("click", () => this.onLeaveRoom());
    }

    renderMatchOver(data) {
        this.endOverlay.classList.remove("hidden");
        const players = [...(data.players || [])].sort((a, b) => Number(a.final_rank || 99) - Number(b.final_rank || 99));
        const rows = players.map((player) => `
            <li class="match-rank-row ${player.final_rank === 1 ? "winner" : ""}">
                <span><b>第 ${player.final_rank} 名</b> ${escapeHtml(player.name || player.uid)}</span>
                <strong>${formatSignedScore(player.cumulative_score || 0)}${player.rank_delta ? ` · 段位 ${formatSignedScore(player.rank_delta)}` : ""}</strong>
            </li>
        `).join("");
        const rematchControls = data.can_rematch ? `
            <div id="rematch-status" class="next-round-status">等待四位玩家決定是否同房再來一場</div>
            <button id="rematch-button" type="button">同房再來一場</button>
        ` : `
            <div class="next-round-status">${data.mode === "ranked" ? "排位場結束後必須重新匹配" : "本房間不能再開一場"}</div>
        `;
        const leaveLabel = data.mode === "ranked" ? "回首頁" : "離開牌桌";
        this.endOverlay.innerHTML = `
            <div class="end-panel match-end-panel">
                <h2>東風場結束</h2>
                <p>${escapeHtml(data.winner_name || "最高分玩家")} 獲得整場第一名</p>
                <div class="match-complete-meta">共完成 ${data.hand_count || (data.hands || []).length} 局</div>
                <ul class="match-rank-list">${rows}</ul>
                <div class="end-actions">
                    ${rematchControls}
                    ${data.mode === "ranked" ? '<button id="ranked-requeue-button" type="button">重新排位</button>' : ""}
                    <button id="match-leave-room-button" class="danger-room-button" type="button">${leaveLabel}</button>
                </div>
            </div>
        `;
        document.getElementById("rematch-button")?.addEventListener("click", () => {
            const button = document.getElementById("rematch-button");
            button.disabled = true;
            button.textContent = "等待其他玩家";
            this.onRematch();
        });
        document.getElementById("ranked-requeue-button")?.addEventListener("click", () => this.onLeaveRoom("ranked"));
        document.getElementById("match-leave-room-button").addEventListener("click", () => this.onLeaveRoom());
    }

    renderNextRoundStatus(data) {
        const status = document.getElementById("next-round-status");
        if (!status) return;

        const readyUids = data.ready_uids || [];
        const didVote = readyUids.includes(this.myUid);
        status.textContent = `已準備 ${data.ready_count || 0}/${data.total || 4}`;

        const button = document.getElementById("next-round-button");
        if (!button) return;
        button.disabled = didVote;
        button.textContent = didVote ? "等待其他玩家" : "再來一局";
    }

    renderRematchStatus(data) {
        const status = document.getElementById("rematch-status");
        if (!status) return;

        const readyUids = data.ready_uids || [];
        const didVote = readyUids.includes(this.myUid);
        status.textContent = `同房再戰已準備 ${data.ready_count || 0}/${data.total || 4}`;

        const button = document.getElementById("rematch-button");
        if (!button) return;
        button.disabled = didVote;
        button.textContent = didVote ? "等待其他玩家" : "同房再來一場";
    }

    renderChatHistory(messages) {
        if (!this.chatList) return;
        this.chatList.innerHTML = "";
        if (!messages.length) {
            const empty = document.createElement("div");
            empty.className = "chat-empty";
            empty.textContent = "本局尚無聊天訊息";
            this.chatList.appendChild(empty);
            return;
        }
        messages.forEach((message) => this.chatList.appendChild(this.createChatMessage(message)));
        this.scrollChatToBottom();
    }

    appendChatMessage(message) {
        if (!this.chatList) return;
        this.chatList.querySelector(".chat-empty")?.remove();
        this.chatList.appendChild(this.createChatMessage(message));
        while (this.chatList.children.length > 80) {
            this.chatList.firstElementChild?.remove();
        }
        this.scrollChatToBottom();
    }

    createChatMessage(message) {
        const item = document.createElement("article");
        item.className = "chat-message";
        item.classList.toggle("mine", message.sender_uid === this.myUid);

        const meta = document.createElement("div");
        meta.className = "chat-meta";
        const name = document.createElement("strong");
        name.textContent = message.sender_name || "玩家";
        const time = document.createElement("span");
        time.textContent = formatChatTime(message.created_at);
        meta.append(name, time);
        item.appendChild(meta);

        const bubble = document.createElement("div");
        bubble.className = `chat-bubble chat-${message.kind || "text"}`;

        if (message.kind === "sticker") {
            const sticker = document.createElement("div");
            sticker.className = "chat-sticker";
            sticker.textContent = message.sticker_label || "貼圖";
            bubble.appendChild(sticker);
        } else if (message.kind === "image" && message.image?.data_url) {
            const image = document.createElement("img");
            image.src = message.image.data_url;
            image.alt = message.image.name || "聊天圖片";
            image.loading = "lazy";
            bubble.appendChild(image);
            if (message.caption) {
                const caption = document.createElement("p");
                caption.textContent = message.caption;
                bubble.appendChild(caption);
            }
        } else {
            bubble.textContent = message.text || "";
        }

        item.appendChild(bubble);
        return item;
    }

    scrollChatToBottom() {
        this.chatList.scrollTop = this.chatList.scrollHeight;
    }
}

function formatHistory(event, namesByIdx) {
    const name = namesByIdx.get(event.player_idx) || `玩家${event.player_idx ?? ""}`;
    if (event.type === "ROUND_START") return "本局開始";
    if (event.type === "DRAW") return event.hidden ? `${name} 摸牌` : `${name} 摸 ${tileLabel(event.tile)}`;
    if (event.type === "FLOWER") return `${name} 補花 ${tileLabel(event.tile)}`;
    if (event.type === "TING") return `${name} 宣告聽牌（打 ${tileLabel(event.tile)}）`;
    if (event.type === "DISCARD") return `${name} 打出 ${tileLabel(event.tile)}`;
    if (event.type === "CLAIM") return `${name} ${ACTION_LABELS[event.action] || event.action} ${tileLabel(event.tile)}`;
    if (event.type === "SELF_ACTION") return `${name} ${ACTION_LABELS[event.action] || event.action} ${tileLabel(event.tile)}`;
    if (event.type === "HU") return `${name} 胡 ${tileLabel(event.tile)}`;
    if (event.type === "DRAW_GAME") return "流局";
    return event.type;
}

function tileLabel(tile) {
    return TILE_LABELS[tile] ?? tile ?? "";
}

function statusBadges(player) {
    const badges = [];
    if (player.rank_name) {
        badges.push(`<em class="status-badge rank">${escapeHtml(player.rank_name)}</em>`);
    }
    if (player.connected === false) {
        badges.push('<em class="status-badge offline">離線</em>');
    }
    if (player.auto_play_enabled) {
        badges.push('<em class="status-badge ai">離線AI</em>');
    } else if (player.ai_enabled) {
        badges.push('<em class="status-badge ai">AI</em>');
    }
    if (player.guoshui) {
        badges.push('<em class="status-badge guoshui">過水</em>');
    }
    if (player.di_ting) {
        badges.push('<em class="status-badge ting">地聽</em>');
    } else if (player.declared_ting) {
        badges.push('<em class="status-badge ting">聽牌</em>');
    }
    return badges.join("");
}

function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

function formatSignedScore(value) {
    const score = Number(value || 0);
    return score > 0 ? `+${score}` : String(score);
}

function formatChatTime(value) {
    const date = value ? new Date(Number(value) * 1000) : new Date();
    return date.toLocaleTimeString("zh-TW", { hour: "2-digit", minute: "2-digit" });
}
