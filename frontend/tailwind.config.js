import lineClamp from '@tailwindcss/line-clamp';

/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Premium Electric Indigo Palette
        'electric-indigo': {
          50: '#eef2ff',
          100: '#e0e7ff',
          200: '#c7d2fe',
          300: '#a5b4fc',
          400: '#818cf8',
          500: '#6366f1',  // Primary brand color
          600: '#4f46e5',
          700: '#4338ca',
          800: '#3730a3',
          900: '#312e81',
          950: '#1e1b4b',
        },
        // Premium gradients background
        'dark-slate': {
          800: '#1e293b',
          900: '#0f172a',
          950: '#020617',
        }
      },
      backgroundImage: {
        // Glassmorphic gradient backgrounds
        'glass-gradient': 'linear-gradient(135deg, rgba(255, 255, 255, 0.1) 0%, rgba(255, 255, 255, 0.05) 100%)',
        'premium-gradient': 'linear-gradient(135deg, #0f172a 0%, #312e81 50%, #0f172a 100%)',
        'card-gradient': 'linear-gradient(180deg, rgba(0, 0, 0, 0.8) 0%, rgba(0, 0, 0, 0.2) 50%, transparent 100%)',
      },
      backdropBlur: {
        xs: '2px',
      },
      borderRadius: {
        '4xl': '2rem',
      },
      boxShadow: {
        'glass': '0 8px 32px 0 rgba(31, 38, 135, 0.37)',
        'glow-indigo': '0 0 20px rgba(99, 102, 241, 0.4)',
        'glow-purple': '0 0 20px rgba(168, 85, 247, 0.4)',
      },
      animation: {
        'pulse-slow': 'pulse 4s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'float': 'float 6s ease-in-out infinite',
      },
      keyframes: {
        float: {
          '0%, 100%': { transform: 'translateY(0px)' },
          '50%': { transform: 'translateY(-20px)' },
        }
      }
    },
  },
  plugins: [
    // Add line clamp for bio truncation
    lineClamp,
  ],
}
