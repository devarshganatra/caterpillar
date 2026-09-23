/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Premium Dark Theme Palette
        background: '#09090b', // zinc-950
        foreground: '#fafafa', // zinc-50
        card: '#18181b', // zinc-900
        'card-foreground': '#fafafa',
        primary: '#facc15', // Cat Yellow
        'primary-foreground': '#18181b',
        secondary: '#27272a', // zinc-800
        'secondary-foreground': '#fafafa',
        muted: '#27272a', // zinc-800
        'muted-foreground': '#a1a1aa', // zinc-400
        accent: '#27272a',
        'accent-foreground': '#fafafa',
        destructive: '#ef4444',
        'destructive-foreground': '#fafafa',
        border: '#27272a', // zinc-800
        input: '#27272a',
        ring: '#facc15', // Cat Yellow
        
        // Status colors
        status: {
          normal: '#22c55e', // green-500
          warning: '#f97316', // orange-500
          critical: '#ef4444', // red-500
          offline: '#71717a', // zinc-500
        }
      },
      borderRadius: {
        lg: `var(--radius, 0.5rem)`,
        md: `calc(var(--radius, 0.5rem) - 2px)`,
        sm: `calc(var(--radius, 0.5rem) - 4px)`,
      },
    },
  },
  plugins: [],
}
