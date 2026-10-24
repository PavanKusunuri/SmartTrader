  // Vendored Tailwind runs locally, so the dashboard works with no internet
  // connection. Configure before first paint to avoid a flash of unstyled text.
  tailwind.config = {
    darkMode: ['variant', [
      '@media (prefers-color-scheme: dark) { &:not(.light *) }',
      '&:is(.dark *)',
    ]],
    theme: {
      extend: {
        fontFamily: {
          sans: ['Segoe UI', 'system-ui', '-apple-system', 'Helvetica Neue', 'Arial', 'sans-serif'],
          mono: ['Cascadia Mono', 'Consolas', 'ui-monospace', 'SFMono-Regular', 'monospace'],
        },
      },
    },
  };
