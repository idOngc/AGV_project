/*
 * 共用前端工具。
 * 每页 <script src="app.js"></script>,然后用全局对象 App.{api, showModal, ...}。
 * Vue 接入后会整体抛弃。
 */

(function (global) {
    const PAGES = [
        { href: "dashboard.html",   label: "AGV 控制台" },
        { href: "map.html",         label: "实时地图" },
        { href: "materials.html",   label: "物料管理" },
        { href: "ws.html",          label: "库位管理" },
        { href: "call-points.html", label: "呼叫点管理" },
        { href: "inventory.html",   label: "库存" },
        { href: "tasks.html",       label: "任务历史" },
    ];

    /* ---------- 登录态 ---------- */
    const token = localStorage.getItem("token");
    const user = JSON.parse(localStorage.getItem("user") || "null");
    if (!token || !user) {
        // 未登录直接踢回 index
        if (!location.pathname.endsWith("/index.html") && !location.pathname.match(/\/web\/?$/)) {
            location.replace("index.html");
        }
        return;
    }

    /* ---------- fetch 封装 ---------- */
    async function api(method, path, body) {
        const opts = {
            method,
            headers: {
                "Authorization": "Bearer " + token,
                "Content-Type": "application/json",
            },
        };
        if (body !== undefined) opts.body = JSON.stringify(body);
        const resp = await fetch(path, opts);
        if (resp.status === 401) {
            localStorage.clear();
            alert("会话已失效,请重新登录");
            location.replace("index.html");
            throw new Error("401");
        }
        const text = await resp.text();
        const data = text ? JSON.parse(text) : null;
        if (!resp.ok) {
            const msg = (data && (data.msg || data.detail)) || `${resp.status} ${resp.statusText}`;
            throw new Error(msg);
        }
        return data;
    }

    /* ---------- 渲染顶栏 ---------- */
    function renderTopbar(title, activeHref) {
        const top = document.querySelector("header.topbar");
        if (!top) return;
        const right = top.querySelector("div") || document.createElement("div");
        right.innerHTML = "";
        // 把所有页面链接渲染进右侧
        PAGES.forEach(p => {
            const a = document.createElement("a");
            a.href = p.href;
            a.textContent = p.label;
            a.className = "topbar-link" + (activeHref === p.href ? " active" : "");
            right.appendChild(a);
        });
        const userSpan = document.createElement("span");
        userSpan.className = "user";
        userSpan.textContent = `${user.username} (${user.role})`;
        right.appendChild(userSpan);
        const btn = document.createElement("button");
        btn.className = "ghost";
        btn.textContent = "退出";
        btn.addEventListener("click", () => {
            localStorage.clear();
            location.replace("index.html");
        });
        right.appendChild(btn);
        top.appendChild(right);

        const h1 = top.querySelector("h1");
        if (h1 && title) h1.textContent = title;
    }

    /* ---------- 弹窗 ---------- */
    function showModal(id) { document.getElementById(id).classList.remove("hidden"); }
    function hideModal(id) { document.getElementById(id).classList.add("hidden"); }

    function bindModalCloseButtons() {
        document.querySelectorAll("[data-close]").forEach(btn => {
            btn.addEventListener("click", () => hideModal(btn.dataset.close));
        });
    }

    /* ---------- Tab ----------
     * callbacks: { tabKey: fn(), ... }  切到对应 tab 时同步调一次
     * 也会在初始化时立刻调一次当前 active tab 对应的回调
     */
    function bindTabs(callbacks = {}) {
        document.querySelectorAll(".tabs").forEach(tabBar => {
            tabBar.addEventListener("click", e => {
                const tab = e.target.closest(".tab");
                if (!tab) return;
                const key = tab.dataset.tab;
                tabBar.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t === tab));
                document.querySelectorAll(".tab-panel").forEach(p => {
                    p.classList.toggle("active", p.dataset.panel === key);
                });
                const cb = callbacks[key];
                if (typeof cb === "function") cb();
            });
        });
    }

    /* ---------- HTML escape ---------- */
    function escapeHtml(s) {
        if (s === null || s === undefined) return "";
        return String(s).replace(/[&<>"']/g, c => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
        }[c]));
    }

    /* ---------- 时间格式化 ---------- */
    function fmtTime(s) {
        if (!s) return "-";
        const d = new Date(s);
        const pad = n => String(n).padStart(2, "0");
        return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
    }

    /* ---------- 短 UUID 生成 (用于"新增"时自动填) ---------- */
    function shortUuid() {
        return ([1e7] + -1e3 + -4e3 + -8e3 + -1e11).replace(/[018]/g, c =>
            (c ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> c / 4).toString(16)
        ).replace(/-/g, "").substring(0, 16);
    }

    /* ---------- 自动刷新 ---------- */
    function makeAutoRefresh(fn, intervalMs, checkboxId) {
        let timer = null;
        const cb = document.getElementById(checkboxId);
        function apply() {
            if (timer) { clearInterval(timer); timer = null; }
            if (!cb || cb.checked) timer = setInterval(fn, intervalMs);
        }
        if (cb) cb.addEventListener("change", apply);
        return apply;
    }

    /* ---------- 业务枚举 ----------
     * 所有 lookup 用 toIntKey 兼容 "1" / 1 两种 key,
     * 防 enum 序列化方式变化造成的隐性 bug。
     */
    function toIntKey(v) { return parseInt(v, 10); }

    const BUSINESS_TYPE = {
        1: { short: "送空→库",  long: "呼叫点→库位 送空托",  en: "SEND_EMPTY_TO_WS" },
        2: { short: "取料",      long: "库位→呼叫点 送料",    en: "FETCH_MATERIAL_TO_CP" },
        3: { short: "取空托",    long: "库位→呼叫点 送空托",  en: "FETCH_EMPTY_TO_CP" },
        4: { short: "送料→库",  long: "呼叫点→库位 送料",    en: "SEND_MATERIAL_TO_WS" },
    };
    function btLabel(v, mode = "short") {
        const item = BUSINESS_TYPE[toIntKey(v)];
        return item ? item[mode] : String(v);
    }

    const WS_TYPE_NAME = { 1: "扫码库位/web", 2: "缓存库位", 3: "铁屑库位", 4: "仓库库位" };
    const PALLET_BIND_MODE_NAME = { 1: "母子托", 2: "母托", 3: "子托" };
    const FUNC_MODE_NAME = { 1: "Load 上料", 2: "Unload 下料", 3: "Load&Unload" };
    const AGV_MODE_NAME = { 1: "叉车", 2: "顶升车", 3: "拖车", 4: "翻转车" };

    const INVENTORY_STATUS = {
        0: { name: "未启用",     code: "DISABLED",      color: "#9aa0a6" },
        1: { name: "空库位",     code: "EMPTY_SLOT",    color: "#cfd4dc" },
        2: { name: "只有母托",   code: "EMPTY_PALLET",  color: "#66c2a5" },
        3: { name: "有托盘有料", code: "FULL_MATERIAL", color: "#5dd1ce" },
        4: { name: "待分配",     code: "PENDING_ALLOC", color: "#ed4858" },
        5: { name: "占用中",     code: "IN_USE",        color: "#f4c44b" },
    };
    function invStatus(v) {
        return INVENTORY_STATUS[toIntKey(v)] || { name: "?", code: "EMPTY_SLOT", color: "#cfd4dc" };
    }

    const CP_RUN_STATUS = {
        0: { name: "未呼叫",      color: "#28a745" },
        1: { name: "正在使用中",  color: "#dc3545" },
        9: { name: "异常",        color: "#ed4858" },
    };
    function cpStatus(v) {
        return CP_RUN_STATUS[toIntKey(v)] || { name: "?", color: "#888" };
    }

    /* ---------- Task 状态 ----------
     * 和后端 TaskStatus 枚举一一对应。cls 提供给 CSS 用 task-status-XXX 着色。
     */
    const TASK_STATUS = {
        0: { name: "INIT",      cn: "待下发", cls: "task-status-INIT" },
        1: { name: "RUNNING",   cn: "执行中", cls: "task-status-RUNNING" },
        2: { name: "PAUSED",    cn: "已暂停", cls: "task-status-PAUSED" },
        3: { name: "COMPLETED", cn: "已完成", cls: "task-status-COMPLETED" },
        4: { name: "FAILED",    cn: "失败",   cls: "task-status-FAILED" },
        5: { name: "CANCELED",  cn: "已取消", cls: "task-status-CANCELED" },
    };
    function taskStatus(v) {
        return TASK_STATUS[toIntKey(v)] || { name: "?", cn: "?", cls: "" };
    }

    /* ---------- TaskStep 状态 ---------- */
    const TASK_STEP_STATUS = {
        0: { name: "待执行", cls: "step-status-PENDING", color: "#e0e3e9" },
        1: { name: "执行中", cls: "step-status-RUNNING", color: "#5b8def" },
        2: { name: "已完成", cls: "step-status-DONE",    color: "#66c2a5" },
        3: { name: "失败",   cls: "step-status-FAILED",  color: "#ed4858" },
        4: { name: "已跳过", cls: "step-status-SKIPPED", color: "#f4c44b" },
    };
    function stepStatus(v) {
        return TASK_STEP_STATUS[toIntKey(v)] || { name: "?", cls: "", color: "#888" };
    }

    const TASK_TYPE_NAME = {
        1: "NAVIGATE", 2: "JACK_LOAD", 3: "JACK_UNLOAD",
        4: "FORK_LOAD", 5: "FORK_UNLOAD",
        6: "ROLLER_LOAD", 7: "ROLLER_UNLOAD",
        99: "CUSTOM",
    };

    /* ---------- AGV 运行态 ---------- */
    const AGV_RUN_STATE = {
        0: { name: "未知",    color: "#9aa0a6" },
        1: { name: "空闲",    color: "#66c2a5" },
        2: { name: "执行中",  color: "#5b8def" },
        3: { name: "暂停",    color: "#f4c44b" },
        4: { name: "充电中",  color: "#5dd1ce" },
        5: { name: "低电量",  color: "#ed7e3b" },
        6: { name: "离线",    color: "#9aa0a6" },
        7: { name: "故障",    color: "#ed4858" },
    };
    function agvRunState(v) {
        return AGV_RUN_STATE[toIntKey(v)] || { name: "?", color: "#888" };
    }

    function lookup(map, v, fallback) {
        const r = map[toIntKey(v)];
        return r !== undefined ? r : (fallback !== undefined ? fallback : v);
    }

    /* ---------- 导出 ---------- */
    global.App = {
        token, user, api,
        renderTopbar, bindModalCloseButtons, bindTabs,
        showModal, hideModal,
        escapeHtml, fmtTime, shortUuid, makeAutoRefresh,
        // 工具
        toIntKey, lookup,
        // 业务枚举 + 辅助 lookup
        BUSINESS_TYPE, btLabel,
        WS_TYPE_NAME, PALLET_BIND_MODE_NAME, FUNC_MODE_NAME, AGV_MODE_NAME,
        INVENTORY_STATUS, invStatus,
        CP_RUN_STATUS, cpStatus,
        TASK_STATUS, taskStatus,
        TASK_STEP_STATUS, stepStatus,
        TASK_TYPE_NAME,
        AGV_RUN_STATE, agvRunState,
    };
})(window);
