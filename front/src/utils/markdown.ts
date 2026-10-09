import { Marked, type Tokens } from 'marked'
import hljs from 'highlight.js/lib/common'
import 'highlight.js/styles/github.css'

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

const marked = new Marked({ breaks: true })

/**
 * URL 协议白名单：链接与图片只放行下列协议，其余（javascript: / vbscript: / data: 等）
 * 一律降级为纯文本。无协议的相对路径与锚点视为安全。
 * marked 自带的 cleanUrl 只做 encodeURI，不校验协议，故必须自行过滤（否则
 * `[x](javascript:alert(1))` 会渲染成可点击的 javascript: 链接 → v-html 场景下是 XSS）。
 */
const SAFE_PROTOCOLS = ['http:', 'https:', 'mailto:', 'tel:']

/** 通过协议白名单则返回原 URL，否则返回 null（调用方降级为纯文本） */
function safeUrl(raw: string | null | undefined): string | null {
  const href = (raw ?? '').trim()
  if (!href) {
    return null
  }
  // 先剔除空白与控制字符再判定协议：`java\nscript:` 这类写法会被浏览器忽略换行后执行
  const compact = href.replace(/[\u0000- ]/g, '')
  const match = /^([a-zA-Z][a-zA-Z0-9+.-]*):/.exec(compact)
  if (!match) {
    return href // 无协议：相对路径 / 锚点
  }
  return SAFE_PROTOCOLS.includes(`${match[1].toLowerCase()}:`) ? href : null
}

marked.use({
  renderer: {
    /** 代码块：highlight.js 高亮；未知语言回退为转义后的纯文本 */
    code({ text, lang, escaped }: Tokens.Code): string {
      const language = (lang ?? '').trim().split(/\s+/)[0]
      if (!escaped && language && hljs.getLanguage(language)) {
        const { value } = hljs.highlight(text, { language, ignoreIllegals: true })
        return `<pre><code class="hljs language-${language}">${value}</code></pre>\n`
      }
      const body = escaped ? text : escapeHtml(text)
      const cls = language ? ` class="hljs language-${escapeHtml(language)}"` : ' class="hljs"'
      return `<pre><code${cls}>${body}</code></pre>\n`
    },
    /** 原始 HTML 按字面展示（收敛注入面：聊天内容不需要内联 HTML） */
    html({ text }: Tokens.HTML | Tokens.Tag): string {
      return escapeHtml(text)
    },
    /** 链接：协议白名单，不合法时只留文字（不可点击） */
    link({ href, title, text, tokens, autolink }: Tokens.Link): string {
      const label = autolink ? escapeHtml(text) : this.parser.parseInline(tokens)
      const url = safeUrl(href)
      if (url === null) {
        return label
      }
      const titleAttr = title ? ` title="${escapeHtml(title)}"` : ''
      return `<a href="${escapeHtml(url)}"${titleAttr}>${label}</a>`
    },
    /** 图片：同样过滤协议，不合法时只留 alt 文字 */
    image({ href, title, text }: Tokens.Image): string {
      const alt = escapeHtml(text ?? '')
      const url = safeUrl(href)
      if (url === null) {
        return alt
      }
      const titleAttr = title ? ` title="${escapeHtml(title)}"` : ''
      return `<img src="${escapeHtml(url)}" alt="${alt}"${titleAttr}>`
    },
  },
})

/** Markdown → HTML（结果经 v-html 注入；安全性依赖上方 renderer 的转义策略） */
export function renderMarkdown(text: string): string {
  return marked.parse(text, { async: false })
}
