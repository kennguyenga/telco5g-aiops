/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      fontFamily: {
        mono: ['JetBrains Mono', 'ui-monospace', 'SFMono-Regular', 'monospace'],
        sans: ['Manrope', 'ui-sans-serif', 'system-ui', 'sans-serif'],
      },
      colors: {
        // Navy / slate ramp — calm NOC console surface
        ink: {
          950: '#070b14',
          900: '#0b1120',  // app background
          800: '#131c30',  // cards / surfaces
          700: '#202b44',  // borders
          600: '#2c3a59',
          500: '#3c4d72',
          400: '#8a99b8',  // muted text
        },
        // Accent repurposed from green → console blue (re-skins every component)
        phosphor: { DEFAULT: '#5e9bff', dim: '#4a7fd6', dark: '#27407a' },
        amber: { signal: '#f5a623', dim: '#b07d23' },
        alert: '#f26d6d',
        ok: '#34d399',
        paper: '#e9eefb',     // near-white primary text
      },
      borderRadius: { card: '0.75rem' },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'fade-in': 'fadeIn 0.3s ease-out',
        'slide-up': 'slideUp 0.4s cubic-bezier(0.16, 1, 0.3, 1)',
      },
      keyframes: {
        fadeIn: { '0%': { opacity: 0 }, '100%': { opacity: 1 } },
        slideUp: { '0%': { opacity: 0, transform: 'translateY(8px)' }, '100%': { opacity: 1, transform: 'translateY(0)' } },
      },
    },
  },
}
