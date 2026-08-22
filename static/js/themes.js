export const THEMES = Object.freeze([
    {
        id: "classic",
        label: "經典翠綠",
        publicLabel: "經典",
        description: "熟悉的綠色牌桌與金色重點",
        color: "#d49a2a",
        themeColor: "#17604d",
    },
    {
        id: "teahouse",
        label: "復古茶館",
        publicLabel: "茶館",
        description: "溫暖木質與沉穩茶綠",
        color: "#d8b56b",
        themeColor: "#42583a",
    },
    {
        id: "cyber",
        label: "霓虹夜城",
        publicLabel: "霓虹",
        description: "深藍背景與明亮青色光芒",
        color: "#55e6ff",
        themeColor: "#132849",
    },
    {
        id: "imperial",
        label: "曜金宮廷",
        publicLabel: "曜金",
        description: "酒紅底色與華麗金色細節",
        color: "#f2c35d",
        themeColor: "#641d2b",
    },
]);

const THEMES_BY_ID = new Map(THEMES.map((theme) => [theme.id, theme]));

export function normalizeThemeId(themeId) {
    return THEMES_BY_ID.has(themeId) ? themeId : "classic";
}

export function getTheme(themeId) {
    return THEMES_BY_ID.get(normalizeThemeId(themeId));
}

export function playerThemeClass(themeId) {
    return `player-theme-${normalizeThemeId(themeId)}`;
}

export function applyDocumentTheme(themeId) {
    const theme = getTheme(themeId);
    document.documentElement.dataset.theme = theme.id;
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) {
        meta.setAttribute("content", theme.themeColor);
    }
    return theme;
}
