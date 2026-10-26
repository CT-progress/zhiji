<script setup>
import { ref, onMounted } from 'vue'
import { useCoreStore } from '../stores/core.js'
import { useUiStore } from '../stores/ui.js'
import { api } from '../lib/api.js'
import { X, Cpu, Folder, SlidersHorizontal, KeyRound } from 'lucide-vue-next'

const core = useCoreStore()
const ui = useUiStore()

const TABS = [
  { key: 'models', title: '模型', sub: '管理 LLM 模型：添加、编辑、连通测试与默认模型', icon: Cpu },
  { key: 'path', title: '保存路径', sub: '设置笔记与转写缓存的保存位置', icon: Folder },
  { key: 'general', title: '通用', sub: '音频转写参数与内容平台开关', icon: SlidersHorizontal },
  { key: 'cookies', title: 'Cookie', sub: '管理各内容平台的登录态（Cookie）', icon: KeyRound },
]

const editingModelName = ref(null)
const mf = ref({ name: '', key: '', base: '', model: '', note: '', default: false })
const outputDir = ref('')
const whisperModel = ref('small')
const whisperDevice = ref('cpu')
const keepAudio = ref(false)
const platformToggles = ref({ bilibili: false, zhihu: false, douyin: false, xiaohongshu: false })

const COOKIE_PLATFORMS = [
  { key: 'douyin', name: '抖音', required: '需包含 sessionid', placeholder: '粘贴抖音 Cookie（需包含 sessionid）' },
  { key: 'bilibili', name: 'B 站', required: '需包含 SESSDATA，配置后优先使用官方字幕', placeholder: '粘贴 B 站 Cookie（需包含 SESSDATA）' },
  { key: 'zhihu', name: '知乎', required: '需包含 d_c0', placeholder: '粘贴知乎 Cookie（需包含 d_c0）' },
  { key: 'xiaohongshu', name: '小红书', required: '需包含 web_session', placeholder: '粘贴小红书 Cookie（需包含 web_session）' },
]
const cookieStates = ref({})
const cookieInputs = ref({})
const cookieHealth = ref({})
const currentTab = ref('models')

function toast(msg) { ui.showToast(msg) }
function healthLine(h) {
  if (!h) return ''
  const st = h.state || ''
  if (st === 'ok') return '正常' + (h.detail ? '：' + h.detail : '')
  if (st === 'expired') return '已失效' + (h.detail ? '：' + h.detail : '')
  if (st === 'error') return '异常' + (h.detail ? '：' + h.detail : '')
  return ''
}

function selectTab(key) {
  currentTab.value = key
  if (key === 'path') outputDir.value = (core.settings || {}).output_dir || ''
  if (key === 'general') {
    const s = core.settings || { whisper_model: 'small', whisper_device: 'cpu', keep_audio: false, platforms: {} }
    whisperModel.value = s.whisper_model || 'small'
    whisperDevice.value = s.whisper_device || 'cpu'
    keepAudio.value = !!s.keep_audio
    const p = s.platforms || {}
    platformToggles.value = { bilibili: !!p.bilibili, zhihu: !!p.zhihu, douyin: !!p.douyin, xiaohongshu: !!p.xiaohongshu }
  }
  if (key === 'cookies') loadCookiesPanel()
}

function fillModelForm(name) {
  const m = core.models.find((x) => x.name === name)
  if (!m) return
  mf.value = { name: m.name, key: '', base: m.base_url, model: m.model, note: m.note || '', default: !!m.default }
}
function editModel(name) { editingModelName.value = name; fillModelForm(name) }
function cancelEdit() { editingModelName.value = null; mf.value = { name: '', key: '', base: '', model: '', note: '', default: false } }

async function saveModel() {
  const payload = { name: mf.value.name.trim(), base_url: mf.value.base.trim(), model: mf.value.model.trim(), note: mf.value.note.trim(), enabled: true, default: mf.value.default }
  if (!payload.name || !payload.base_url || !payload.model) { toast('名称 / Base URL / 模型 ID 必填'); return }
  const key = mf.value.key.trim()
  if (key || !editingModelName.value) payload.api_key = key
  try {
    if (!editingModelName.value) {
      const data = await api('/api/models', { method: 'POST', body: JSON.stringify(payload) })
      if (!core.model) core.model = data.model.name
    } else {
      await api('/api/models/' + encodeURIComponent(editingModelName.value), { method: 'PUT', body: JSON.stringify(payload) })
    }
    toast('模型已保存')
    editingModelName.value = null
    await core.loadModels()
  } catch (err) { toast(err.message) }
}

async function setDefault(name) {
  try {
    await api('/api/models/' + encodeURIComponent(name) + '/set-default', { method: 'POST' })
    toast('默认模型已切换')
    if (!core.model) core.model = name
    await core.loadModels()
  } catch (err) { toast(err.message) }
}

async function removeModel(name) {
  if (!window.confirm('确定删除模型 ' + name + '？')) return
  try {
    await api('/api/models/' + encodeURIComponent(name), { method: 'DELETE' })
    if (core.model === name) core.model = null
    await core.loadModels()
  } catch (err) { toast(err.message) }
}

async function checkCredential(kind, name) {
  toast('检测中…')
  try {
    const data = await api('/api/credentials/check', { method: 'POST', body: JSON.stringify({ kind, name }) })
    const result = data.result || {}
    toast((result.label || '检测完成') + (result.detail ? '：' + result.detail : ''))
    if (kind === 'llm') await core.loadModels()
    if (kind === 'platform') loadCookiesPanel()
  } catch (err) { toast(err.message || '检测失败') }
}

async function savePath() {
  try { await core.saveSettings({ output_dir: outputDir.value.trim() }); toast('保存路径已更新') } catch (err) { toast(err.message) }
}

async function saveGeneral() {
  const payload = { whisper_model: whisperModel.value, whisper_device: whisperDevice.value, keep_audio: keepAudio.value, platforms: { ...platformToggles.value } }
  try { await core.saveSettings(payload); await core.loadPlatforms(); toast('设置已保存') } catch (err) { toast(err.message) }
}

async function loadCookiesPanel() {
  try {
    const data = await api('/api/credentials/health')
    cookieHealth.value = {}
    ;(data.platforms || []).forEach((item) => { cookieHealth.value[item.key] = item })
  } catch (e) {}
  cookieStates.value = {}
  for (const p of COOKIE_PLATFORMS) {
    cookieInputs.value[p.key] = ''
    try {
      const data = await api('/api/cookies/' + p.key)
      cookieStates.value[p.key] = { configured: data.configured, cookieLength: data.cookie_length }
    } catch (e) {
      cookieStates.value[p.key] = { configured: false, error: true }
    }
  }
}

async function saveCookie(key) {
  const cookie = (cookieInputs.value[key] || '').trim()
  if (!cookie) { toast('请输入 Cookie'); return }
  try {
    await api('/api/cookies/' + key, { method: 'POST', body: JSON.stringify({ cookie }) })
    toast('Cookie 已保存')
    await core.loadPlatforms()
    loadCookiesPanel()
  } catch (e) { toast(e.message || '保存失败') }
}

async function deleteCookie(key) {
  try {
    await api('/api/cookies/' + key, { method: 'DELETE' })
    toast('Cookie 已清除')
    await core.loadPlatforms()
    loadCookiesPanel()
  } catch (e) { toast('清除失败') }
}

onMounted(() => { currentTab.value = ui.settingsTab; selectTab(ui.settingsTab) })
</script>

<template>
  <div class="settings-page">
    <aside class="settings-nav">
      <div class="settings-nav-group">设置</div>
      <button v-for="t in TABS" :key="t.key" class="settings-nav-item" :class="{ active: currentTab === t.key }" type="button" @click="selectTab(t.key)">
        <component :is="t.icon" :size="18" /><span>{{ t.title }}</span>
      </button>
    </aside>
    <div class="settings-main">
      <header class="settings-head">
        <div>
          <h2>{{ TABS.find((t) => t.key === currentTab).title }}</h2>
          <div class="settings-sub">{{ TABS.find((t) => t.key === currentTab).sub }}</div>
        </div>
        <button class="icon-btn" type="button" aria-label="关闭" @click="ui.closeSettings()"><X :size="20" /></button>
      </header>
      <div class="settings-body">
        <div v-if="currentTab === 'models'">
          <div v-for="m in core.models" :key="m.name" class="model-row">
            <div class="model-row-top">
              <span class="model-row-name">{{ m.name }}</span>
              <span v-if="m.default" class="badge">默认</span>
              <span v-if="!m.enabled" class="badge" style="background:var(--surface-2);color:var(--muted)">停用</span>
            </div>
            <div class="model-row-meta">{{ m.model }} ｜ {{ m.base_url }} ｜ Key: {{ m.api_key || '未设置' }}</div>
            <div v-if="healthLine(m.health)" class="cookie-hint">{{ healthLine(m.health) }}</div>
            <div class="model-row-actions">
              <button class="btn small" type="button" @click="editModel(m.name)">编辑</button>
              <button class="btn small" type="button" @click="checkCredential('llm', m.name)">检测</button>
              <button v-if="!m.default" class="btn small" type="button" @click="setDefault(m.name)">设为默认</button>
              <button class="btn small danger" type="button" @click="removeModel(m.name)">删除</button>
            </div>
          </div>
          <div v-if="!core.models.length" class="search-empty">还没有模型，请在下方添加</div>
          <hr style="border-color:var(--border);margin:12px 0">
          <h3 style="margin:0 0 8px;font-size:14px">{{ editingModelName ? '编辑模型' : '添加模型' }}</h3>
          <div class="form-field"><label>名称</label><input v-model="mf.name" placeholder="例如 DeepSeek"></div>
          <div class="form-field"><label>API Key{{ editingModelName ? '（留空则不修改）' : '' }}</label><input v-model="mf.key" type="password" placeholder="sk-..."></div>
          <div class="form-field"><label>Base URL</label><input v-model="mf.base" placeholder="https://api.deepseek.com/v1"></div>
          <div class="form-row">
            <div class="form-field"><label>模型 ID</label><input v-model="mf.model" placeholder="deepseek-chat"></div>
            <div class="form-field"><label>备注</label><input v-model="mf.note" placeholder="可选"></div>
          </div>
          <label class="check-line"><input type="checkbox" v-model="mf.default"> 设为默认模型</label>
          <div style="display:flex;gap:8px;margin-top:8px">
            <button class="btn primary" type="button" @click="saveModel">保存模型</button>
            <button v-if="editingModelName" class="btn" type="button" @click="cancelEdit">取消编辑</button>
          </div>
        </div>

        <div v-else-if="currentTab === 'path'">
          <div class="form-field"><label>笔记输出目录</label><input v-model="outputDir"></div>
          <div class="cookie-hint">笔记 Markdown 与转写缓存都会保存在该目录下，可使用绝对路径。</div>
          <button class="btn primary" type="button" style="margin-top:12px" @click="savePath">保存</button>
        </div>

        <div v-else-if="currentTab === 'general'">
          <h3 style="margin:0 0 10px;font-size:14px">音频转写</h3>
          <div class="form-row">
            <div class="form-field"><label>Whisper 模型</label>
              <select v-model="whisperModel"><option v-for="v in ['tiny','small','medium','large-v3']" :key="v" :value="v">{{ v }}</option></select>
            </div>
            <div class="form-field"><label>Whisper 设备</label>
              <select v-model="whisperDevice"><option value="cpu">cpu</option><option value="cuda">cuda</option></select>
            </div>
          </div>
          <label class="check-line"><input type="checkbox" v-model="keepAudio"> 保留转写后的音频文件</label>
          <h3 style="margin:14px 0 6px;font-size:14px">内容平台</h3>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px">
            <label v-for="(on, key) in platformToggles" :key="key" class="check-line">
              <input type="checkbox" v-model="platformToggles[key]"> {{ { bilibili:'B 站', zhihu:'知乎', douyin:'抖音', xiaohongshu:'小红书' }[key] }}
            </label>
          </div>
          <div class="cookie-hint">小红书部分笔记需要登录 Cookie；未配置时会先尝试公开页面。</div>
          <button class="btn primary" type="button" style="margin-top:12px" @click="saveGeneral">保存设置</button>
        </div>

        <div v-else-if="currentTab === 'cookies'">
          <div v-for="p in COOKIE_PLATFORMS" :key="p.key" class="cookie-card">
            <div class="cookie-card-title">{{ p.name }}<span class="cookie-card-req">{{ p.required }}</span></div>
            <div v-if="cookieStates[p.key] && cookieStates[p.key].configured" style="color:var(--accent);margin:0 0 8px">已配置（{{ cookieStates[p.key].cookieLength }} 字节）</div>
            <div v-if="cookieHealth[p.key]" class="cookie-hint">{{ healthLine(cookieHealth[p.key].health) }}</div>
            <div v-if="cookieStates[p.key] && cookieStates[p.key].configured" class="model-row-actions">
              <button class="btn small" type="button" @click="checkCredential('platform', p.key)">检测登录态</button>
              <button class="btn small" type="button" @click="deleteCookie(p.key)">清除 Cookie</button>
            </div>
            <div v-else-if="cookieStates[p.key] && !cookieStates[p.key].error">
              <div class="form-field" style="margin-bottom:8px"><textarea v-model="cookieInputs[p.key]" rows="3" :placeholder="p.placeholder"></textarea></div>
              <button class="btn small" type="button" @click="saveCookie(p.key)">保存 Cookie</button>
            </div>
          </div>
          <div class="cookie-howto">
            <h3>如何获取 Cookie？</h3>
            <ol>
              <li>在浏览器中打开对应平台网站并登录账号；</li>
              <li>按 F12 打开开发者工具，切换到「网络 / Network」面板；</li>
              <li>刷新页面，任选一个发往该平台域名的请求；</li>
              <li>在「请求标头 / Request Headers」中找到 Cookie 一行，整行复制；</li>
              <li>粘贴到上方对应平台的输入框并保存。</li>
            </ol>
            <div class="cookie-hint">Cookie 仅保存在本机 data/ 目录，不会上传。失效后重新复制保存即可。</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
