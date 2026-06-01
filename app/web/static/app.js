/*
 * 共用前端工具。
 * 每页 <script src="app.js"></script>,然后用全局对象 App.{api, showModal, ...}。
 * Vue 接入后会整体抛弃。
 */

(function (global) {
    const PAGES = [
        { href: "dashboard.html",   label: "AGV 控制台" },
        { href: "materials.html",   label: "物料管理" },
        { href: "ws.html",          label: "库位管理" },
        { href: "call-points.html", label: "呼叫点管理" },
        { href: "inventory.html",   label: "库存 / 放料图" },
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

    /* ---------- Tab ---------- */
    function bindTabs() {
        document.querySelectorAll(".tabs").forEach(tabBar => {
            tabBar.addEventListener("click", e => {
                const tab = e.target.closest(".tab");
                if (!tab) return;
                const key = tab.dataset.tab;
                tabBar.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t === tab));
                document.querySelectorAll(".tab-panel").forEach(p => {
                    p.classList.toggle("active", p.dataset.panel === key);
                });
                if (typeof tab._onShow === "function") tab._onShow();
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

    /* ---------- 业务枚举 ---------- */
    const BUSINESS_TYPE_NAME = {
        1: "呼叫点→库位 送空托 (SEND_EMPTY_TO_WS)",
        2: "库位→呼叫点 送料 (FETCH_MATERIAL_TO_CP)",
        3: "库位→呼叫点 送空托 (FETCH_EMPTY_TO_CP)",
        4: "呼叫点→库位 送料 (SEND_MATERIAL_TO_WS)",
    };
    const WS_TYPE_NAME = { 1: "扫码库位/web", 2: "缓存库位", 3: "铁屑库位", 4: "仓库库位" };
    const PALLET_BIND_MODE_NAME = { 1: "母子托", 2: "母托", 3: "子托" };
    const FUNC_MODE_NAME = { 1: "Load 上料", 2: "Unload 下料", 3: "Load&Unload" };
    const AGV_MODE_NAME = { 1: "叉车", 2: "顶升车", 3: "拖车", 4: "翻转车" };
    const INVENTORY_STATUS = {
        0: { name: "未启用",    code: "DISABLED" },
        1: { name: "空库位",    code: "EMPTY_SLOT" },
        2: { name: "只有母托",  code: "EMPTY_PALLET" },
        3: { name: "有托盘有料",code: "FULL_MATERIAL" },
        4: { name: "待分配",    code: "PENDING_ALLOC" },
        5: { name: "占用中",    code: "IN_USE" },
    };
    const CP_RUN_STATUS = {
        0: { name: "未呼叫", color: "#28a745" },
        1: { name: "正在使用中", color: "#dc3545" },
        9: { name: "异常", color: "#ed4858" },
    };

    /* ---------- 导出 ---------- */
    global.App = {
        token, user, api,
        renderTopbar, bindModalCloseButtons, bindTabs,
        showModal, hideModal,
        escapeHtml, fmtTime, shortUuid, makeAutoRefresh,
        // 枚举
        BUSINESS_TYPE_NAME, WS_TYPE_NAME, PALLET_BIND_MODE_NAME,
        FUNC_MODE_NAME, AGV_MODE_NAME, INVENTORY_STATUS, CP_RUN_STATUS,
    };
})(window);
