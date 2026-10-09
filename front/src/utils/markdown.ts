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
  },
})

/** Markdown → HTML（结果经 v-html 注入；安全性依赖上方 renderer 的转义策略） */
export function renderMarkdown(text: string): string {
  return marked.parse(text, { async: false })
}
