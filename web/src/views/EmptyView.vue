<template>
  <div class="empty-view">
    <a-result status="404" title="页面不存在" sub-title="您访问的页面不存在">
      <template #extra>
        <a-button type="primary" @click="$router.push('/agent')">返回首页</a-button>
      </template>
    </a-result>
  </div>
</template>

<style scoped>
.empty-view { display: flex; align-items: center; justify-content: center; height: 100vh; }
</style>
