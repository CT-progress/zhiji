// Markdown 渲染：从原 app.js 原样移植
export function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

export function stripFrontmatter(markdown) {
  const text = String(markdown || '').replace(/^\s+/, '')
  if (text.startsWith('---')) {
    const end = text.indexOf('\n---', 3)
    if (end !== -1) return text.slice(end + 4).replace(/^\n+/, '')
  }
  return markdown
}

function mdInline(text) {
  let out = escapeHtml(text)
  out = out.replace(/`([^`]+)`/g, '<code>$1</code>')
  out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  out = out.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
  out = out.replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
  return out
}

export function renderMarkdown(src) {
  const lines = String(src || '').replace(/\r\n/g, '\n').split('\n')
  const html = []
  let inCode = false
  let codeBuf = []
  let listType = null
  let para = []
  const flushPara = () => {
    if (para.length) { html.push('<p>' + para.map(mdInline).join('<br>') + '</p>'); para = [] }
  }
  const closeList = () => {
    if (listType) { html.push('</' + listType + '>'); listType = null }
  }
  for (const raw of lines) {
    const line = raw.replace(/\s+$/, '')
    if (/^```/.test(line.trim())) {
      if (inCode) {
        html.push('<pre><code>' + escapeHtml(codeBuf.join('\n')) + '</code></pre>')
        codeBuf = []; inCode = false
      } else {
        flushPara(); closeList(); inCode = true
      }
      continue
    }
    if (inCode) { codeBuf.push(raw); continue }
    const trimmed = line.trim()
    if (!trimmed) { flushPara(); closeList(); continue }
    const head = trimmed.match(/^(#{1,4})\s+(.*)$/)
    if (head) {
      flushPara(); closeList()
      const level = head[1].length
      html.push('<h' + level + '>' + mdInline(head[2]) + '</h' + level + '>')
      continue
    }
    if (/^(-{3,}|\*{3,})$/.test(trimmed)) { flushPara(); closeList(); html.push('<hr>'); continue }
    if (trimmed.startsWith('>')) {
      flushPara(); closeList()
      html.push('<blockquote>' + mdInline(trimmed.replace(/^>\s?/, '')) + '</blockquote>')
      continue
    }
    const ul = trimmed.match(/^[-*+]\s+(.*)$/)
    if (ul) {
      flushPara()
      if (listType !== 'ul') { closeList(); html.push('<ul>'); listType = 'ul' }
      html.push('<li>' + mdInline(ul[1]) + '</li>')
      continue
    }
    const ol = trimmed.match(/^\d+[.)]\s+(.*)$/)
    if (ol) {
      flushPara()
      if (listType !== 'ol') { closeList(); html.push('<ol>'); listType = 'ol' }
      html.push('<li>' + mdInline(ol[1]) + '</li>')
      continue
    }
    closeList()
    para.push(trimmed)
  }
  if (inCode) html.push('<pre><code>' + escapeHtml(codeBuf.join('\n')) + '</code></pre>')
  flushPara(); closeList()
  return html.join('\n')
}
