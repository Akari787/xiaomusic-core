import { loadEnv, defineConfig } from 'vitepress'
import AutoSidebar from 'vite-plugin-vitepress-auto-sidebar';
import GitHubIssuesPlugin from './vitepress-plugin-github-issues.mts';

function normalizeBase(rawBase: string): string {
  let base = (rawBase || '').trim()
  if (!base) return '/'

  // Allow providing a full URL via env/vars; VitePress base must be a pathname.
  if (/^https?:\/\//i.test(base)) {
    try {
      base = new URL(base).pathname || '/'
    } catch {
      // keep raw value; we'll normalize slashes below
    }
  }

  if (!base.startsWith('/')) base = `/${base}`
  if (base !== '/' && !base.endsWith('/')) base = `${base}/`
  if (base == '//') base = '/'
  return base
}

function normalizeHostname(rawHostname: string): string {
  const v = (rawHostname || '').trim()
  if (!v) return ''
  try {
    // VitePress sitemap expects an absolute URL.
    return new URL(v).origin
  } catch {
    return ''
  }
}

export default async ({ mode }) => {
  const env = loadEnv(mode || '', process.cwd())
  const siteBaseRaw = process.env.VITE_SITE_BASE || env.VITE_SITE_BASE || '/'
  const siteBase = normalizeBase(siteBaseRaw)

  const siteHostnameRaw = process.env.VITE_SITE_HOSTNAME || env.VITE_SITE_HOSTNAME || 'https://xdocs.hanxi.cc'
  const siteHostname = normalizeHostname(siteHostnameRaw) || 'https://xdocs.hanxi.cc'
  const issuesToken = process.env.VITE_GITHUB_ISSUES_TOKEN || env.VITE_GITHUB_ISSUES_TOKEN || ''
  return defineConfig({
    // GitHub Pages: for project pages you typically need "/<repo-name>/".
    // Override via VITE_SITE_BASE in CI or locally.
    base: siteBase,
    title: "XiaoMusic",
    description: "XiaoMusic doc",
    themeConfig: {
      // https://vitepress.dev/reference/default-theme-config
      nav: [
        { text: 'Guide', link: '/issues' },
        { text: 'Admin', link: 'https://x.hanxi.cc' },
      ],

      socialLinks: [
        { icon: 'github', link: 'https://github.com/hanxi/xiaomusic' }
      ],

      footer: {
        message: '基于 MIT 许可发布',
        copyright: `版权所有 © 2023-${new Date().getFullYear()} 涵曦`
      },
    },
    sitemap: siteHostname ? { hostname: siteHostname } : undefined,
    head: [
      ['script', { defer: true, src: 'https://umami.hanxi.cc/script.js', 'data-website-id': '29cca3f5-e420-432b-adc7-8a1325d31c68' }]
    ],
    lastUpdated: true,
    markdown: {
      lineNumbers: false, // 关闭代码块行号显示
      // 自定义 markdown-it 插件
      config: (md) => {
        md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
          const aIndex = tokens[idx].attrIndex('target');
          if (aIndex < 0) {
            tokens[idx].attrPush(['target', '_self']); // 将默认行为改为不使用 _blank
          } else {
            tokens[idx].attrs![aIndex][1] = '_self'; // 替换 _blank 为 _self
          }
          return self.renderToken(tokens, idx, options);
        };
      },
    },
    logLevel: 'warn',
    vite: {
      plugins: [
        AutoSidebar({
          path: '.',
          collapsed: true,
          titleFromFile: true,
        }),
        GitHubIssuesPlugin({
          repo: 'hanxi/xiaomusic',
          token: issuesToken,
          replaceRules: [
            {
              baseUrl: 'https://github.com/hanxi/xiaomusic/issues',
              targetUrl: '/issues',
            },
          ],
          githubProxy: 'https://gproxy.hanxi.cc/proxy',
        }),
      ],
    }
  })
}
