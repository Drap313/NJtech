import type { Config } from "tailwindcss";
import flattenColorPalette from "tailwindcss/lib/util/flattenColorPalette";

export default {
  darkMode: ["class"],
  content: ["./pages/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./app/**/*.{ts,tsx}", "./src/**/*.{ts,tsx}"],
  prefix: "",
  theme: {
    container: {
      center: true,
      padding: "2rem",
      screens: {
        "2xl": "1400px",
      },
    },
    extend: {
      backgroundImage: {
        'lines-pattern': `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 800 800'%3E%3Cg stroke-width='3.5' stroke='hsla(151, 78%25, 64%25, 0.4)' fill='none'%3E%3Cline x1='0' y1='0' x2='400' y2='400'%3E%3C/line%3E%3Cline x1='400' y1='0' x2='800' y2='400'%3E%3C/line%3E%3Cline x1='800' y1='0' x2='1200' y2='400'%3E%3C/line%3E%3Cline x1='0' y1='400' x2='400' y2='800'%3E%3C/line%3E%3Cline x1='400' y1='400' x2='800' y2='800'%3E%3C/line%3E%3Cline x1='800' y1='400' x2='1200' y2='800'%3E%3C/line%3E%3Cline x1='0' y1='800' x2='400' y2='1200'%3E%3C/line%3E%3Cline x1='400' y1='800' x2='800' y2='1200'%3E%3C/line%3E%3Cline x1='800' y1='800' x2='1200' y2='1200'%3E%3C/line%3E%3C/g%3E%3C/svg%3E")`,
        'lines-pattern-light': `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 800 800'%3E%3Cg stroke-width='3.5' stroke='hsla(160, 65%25, 42%25, 0.28)' fill='none'%3E%3Cline x1='0' y1='0' x2='400' y2='400'%3E%3C/line%3E%3Cline x1='400' y1='0' x2='800' y2='400'%3E%3C/line%3E%3Cline x1='800' y1='0' x2='1200' y2='400'%3E%3C/line%3E%3Cline x1='0' y1='400' x2='400' y2='800'%3E%3C/line%3E%3Cline x1='400' y1='400' x2='800' y2='800'%3E%3C/line%3E%3Cline x1='800' y1='400' x2='1200' y2='800'%3E%3C/line%3E%3Cline x1='0' y1='800' x2='400' y2='1200'%3E%3C/line%3E%3Cline x1='400' y1='800' x2='800' y2='1200'%3E%3C/line%3E%3Cline x1='800' y1='800' x2='1200' y2='1200'%3E%3C/line%3E%3C/g%3E%3C/svg%3E")`,
      },
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        sidebar: {
          DEFAULT: "hsl(var(--sidebar-background))",
          foreground: "hsl(var(--sidebar-foreground))",
          primary: "hsl(var(--sidebar-primary))",
          "primary-foreground": "hsl(var(--sidebar-primary-foreground))",
          accent: "hsl(var(--sidebar-accent))",
          "accent-foreground": "hsl(var(--sidebar-accent-foreground))",
          border: "hsl(var(--sidebar-border))",
          ring: "hsl(var(--sidebar-ring))",
        },
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
      keyframes: {
        "accordion-down": {
          from: {
            height: "0",
          },
          to: {
            height: "var(--radix-accordion-content-height)",
          },
        },
        "accordion-up": {
          from: {
            height: "var(--radix-accordion-content-height)",
          },
          to: {
            height: "0",
          },
        },
        aurora: {
          from: {
            backgroundPosition: "50% 50%, 50% 50%",
          },
          to: {
            backgroundPosition: "350% 50%, 350% 50%",
          },
        },
      },
      animation: {
        "accordion-down": "accordion-down 0.2s ease-out",
        "accordion-up": "accordion-up 0.2s ease-out",
        aurora: "aurora 60s linear infinite",
      },
    },
  },
  plugins: [require("tailwindcss-animate"), addVariablesForColors],
} satisfies Config;

// This plugin adds each Tailwind color as a global CSS variable, e.g. var(--gray-200).
function addVariablesForColors({ addBase, theme }: any) {
  let allColors = flattenColorPalette(theme("colors"));
  let newVars = Object.fromEntries(
    Object.entries(allColors).map(([key, val]) => [`--${key}`, val])
  );

  addBase({
    ":root": newVars,
  });
}
