<template>
  <router-view />
</template>

<script setup lang="ts"></script>

<style>
/* ============================================================
   客户端视觉令牌：渐变 + 轻玻璃质感
   只服务前台（聊天 / 登录 / 历史），与后台 admin-frontend 各自独立。
   ============================================================ */
:root {
  --fc-brand: #4f6bff;
  --fc-brand-2: #7a5cff;
  --fc-glow: rgba(79, 107, 255, 0.26);
  --fc-bg: #eef1ff;
  --fc-text: #1b1f2a;
  --fc-text-2: #5b6478;
  --fc-border: rgba(79, 107, 255, 0.16);
  --fc-glass: rgba(255, 255, 255, 0.72);
  --fc-radius: 16px;
}

/* 用 dvh 跟随移动端地址栏/键盘的可视高度变化，100vh 在 iOS 上会多出一截 */
html,
body,
#app {
  height: 100%;
  height: 100dvh;
  margin: 0;
  font-family: 'Helvetica Neue', Helvetica, 'PingFang SC', 'Microsoft YaHei', Arial, sans-serif;
  color: var(--fc-text);
  background: var(--fc-bg);
  -webkit-text-size-adjust: 100%;
  /* 聊天页自身滚动，禁止整页橡皮筋回弹 */
  overscroll-behavior: none;
}

/* 移动端：输入字号 ≥16px 才不会触发 iOS 聚焦自动放大 */
@media (max-width: 768px) {
  .el-input__inner,
  .el-textarea__inner {
    font-size: 16px;
  }

  .el-input__wrapper {
    min-height: 38px;
  }
}

/* “更多”下拉：跟随顶栏的玻璃语言（菜单挂到 body，只能用全局样式） */
.cs-header-menu {
  padding: 6px !important;
  border-radius: 12px !important;
  border: 1px solid rgba(79, 107, 255, 0.12) !important;
  box-shadow: 0 12px 32px rgba(31, 42, 80, 0.14) !important;
}

.cs-header-menu .el-dropdown-menu__item {
  padding: 8px 12px;
  border-radius: 8px;
  font-size: 13px;
}

.cs-header-menu .el-dropdown-menu__item:not(.is-disabled):hover {
  background: rgba(79, 107, 255, 0.1);
  color: var(--fc-brand);
}

/* 系统开了「减少动态效果」时，全部动画退化为静态 */
@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
  }
}
</style>
