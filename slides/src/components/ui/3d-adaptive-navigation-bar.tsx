import React from 'react'
import { motion, useSpring } from 'framer-motion'

interface NavItem {
  label: string
  id: string
}

export const PillBase: React.FC<{ activeSection: string; navItems: NavItem[]; onSectionClick: (id: string) => void }> = ({ 
  activeSection, 
  navItems,
  onSectionClick 
}) => {
  const targetWidth = Math.min(1200, Math.max(760, navItems.length * 140))
  const pillWidth = useSpring(targetWidth, { stiffness: 220, damping: 25, mass: 1 })
  const pillShift = useSpring(0, { stiffness: 220, damping: 25, mass: 1 })

  return (
    <motion.nav
      className="relative rounded-full"
      style={{
        width: pillWidth,
        height: '56px',
        background: `
          linear-gradient(135deg, 
            #070d16 0%, 
            #0a1420 15%, 
            #0e1b2b 30%, 
            #122337 45%, 
            #152a42 60%, 
            #18314d 75%, 
            #1c3858 90%, 
            #132740 100%
          )
        `,
        boxShadow: `
          0 2px 4px rgba(0, 0, 0, 0.4),
          0 6px 12px rgba(0, 0, 0, 0.5),
          0 12px 24px rgba(0, 0, 0, 0.6),
          0 24px 48px rgba(0, 0, 0, 0.4),
          inset 0 2px 2px rgba(255, 255, 255, 0.08),
          inset 0 -3px 8px rgba(0, 0, 0, 0.6),
          inset 3px 3px 8px rgba(0, 0, 0, 0.5),
          inset -3px 3px 8px rgba(0, 0, 0, 0.4),
          inset 0 -1px 2px rgba(0, 0, 0, 0.5)
        `,
        x: pillShift,
        overflow: 'hidden',
        transition: 'box-shadow 0.3s ease-out',
      }}
    >
      {/* Primary top edge ridge - subtle highlight */}
      <div 
        className="absolute inset-x-0 top-0 rounded-t-full pointer-events-none"
        style={{
          height: '2px',
          background: 'linear-gradient(90deg, rgba(255, 255, 255, 0) 0%, rgba(255, 255, 255, 0.15) 5%, rgba(255, 255, 255, 0.2) 15%, rgba(255, 255, 255, 0.2) 85%, rgba(255, 255, 255, 0.15) 95%, rgba(255, 255, 255, 0) 100%)',
          filter: 'blur(0.3px)',
        }}
      />
      
      {/* Top hemisphere light catch */}
      <div 
        className="absolute inset-x-0 top-0 rounded-full pointer-events-none"
        style={{
          height: '55%',
          background: 'linear-gradient(180deg, rgba(255, 255, 255, 0.08) 0%, rgba(255, 255, 255, 0.04) 30%, rgba(255, 255, 255, 0.02) 60%, rgba(255, 255, 255, 0) 100%)',
        }}
      />
      
      {/* Directional light - top left */}
      <div 
        className="absolute inset-0 rounded-full pointer-events-none"
        style={{
          background: 'linear-gradient(135deg, rgba(255, 255, 255, 0.06) 0%, rgba(255, 255, 255, 0.03) 20%, rgba(255, 255, 255, 0.01) 40%, rgba(255, 255, 255, 0) 65%)',
        }}
      />
      
      {/* Premium gloss reflection - main */}
      <div 
        className="absolute rounded-full pointer-events-none"
        style={{
          left: '18%',
          top: '16%',
          width: '140px',
          height: '14px',
          background: 'radial-gradient(ellipse at center, rgba(255, 255, 255, 0.12) 0%, rgba(255, 255, 255, 0.06) 40%, rgba(255, 255, 255, 0.02) 70%, rgba(255, 255, 255, 0) 100%)',
          filter: 'blur(4px)',
          transform: 'rotate(-12deg)',
        }}
      />
      
      {/* Secondary gloss accent */}
      <div 
        className="absolute rounded-full pointer-events-none"
        style={{
          right: '22%',
          top: '20%',
          width: '80px',
          height: '10px',
          background: 'radial-gradient(ellipse at center, rgba(255, 255, 255, 0.08) 0%, rgba(255, 255, 255, 0.03) 60%, rgba(255, 255, 255, 0) 100%)',
          filter: 'blur(3px)',
          transform: 'rotate(8deg)',
        }}
      />
      
      {/* Left edge illumination */}
      <div 
        className="absolute inset-y-0 left-0 rounded-l-full pointer-events-none"
        style={{
          width: '35%',
          background: 'linear-gradient(90deg, rgba(255, 255, 255, 0.04) 0%, rgba(255, 255, 255, 0.02) 40%, rgba(255, 255, 255, 0.01) 70%, rgba(255, 255, 255, 0) 100%)',
        }}
      />
      
      {/* Right edge shadow */}
      <div 
        className="absolute inset-y-0 right-0 rounded-r-full pointer-events-none"
        style={{
          width: '35%',
          background: 'linear-gradient(270deg, rgba(0, 0, 0, 0.4) 0%, rgba(0, 0, 0, 0.2) 40%, rgba(0, 0, 0, 0.1) 70%, rgba(0, 0, 0, 0) 100%)',
        }}
      />
      
      {/* Bottom curvature - deep shadow */}
      <div 
        className="absolute inset-x-0 bottom-0 rounded-b-full pointer-events-none"
        style={{
          height: '50%',
          background: 'linear-gradient(0deg, rgba(0, 0, 0, 0.5) 0%, rgba(0, 0, 0, 0.3) 25%, rgba(0, 0, 0, 0.15) 50%, rgba(0, 0, 0, 0) 100%)',
        }}
      />

      {/* Bottom edge contact shadow */}
      <div 
        className="absolute inset-x-0 bottom-0 rounded-b-full pointer-events-none"
        style={{
          height: '20%',
          background: 'linear-gradient(0deg, rgba(0, 0, 0, 0.6) 0%, rgba(0, 0, 0, 0) 100%)',
          filter: 'blur(2px)',
        }}
      />

      {/* Inner diffuse glow */}
      <div 
        className="absolute inset-0 rounded-full pointer-events-none"
        style={{
          boxShadow: 'inset 0 0 40px rgba(62, 207, 142, 0.05)',
          opacity: 0.7,
        }}
      />
      
      {/* Micro edge definition */}
      <div 
        className="absolute inset-0 rounded-full pointer-events-none"
        style={{
          boxShadow: 'inset 0 0 0 0.5px rgba(255, 255, 255, 0.12)',
        }}
      />

      {/* Navigation items container */}
      <div
        className="relative z-10 h-full flex items-center justify-center px-4"
        style={{
          fontFamily: '"Elms Sans", sans-serif',
        }}
      >
        {/* Always show all sections */}
        <div className="flex items-center justify-evenly w-full">
          {navItems.map((item) => {
            const isActive = item.id === activeSection
            
            return (
              <motion.button
                key={item.id}
                initial={{ opacity: 1, x: 0 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ 
                  duration: 0.25,
                  ease: 'easeOut'
                }}
                onClick={() => onSectionClick(item.id)}
                className="relative cursor-pointer transition-all duration-200"
                style={{
                  fontSize: isActive ? '14.5px' : '14px',
                  fontWeight: isActive ? 680 : 510,
                  color: isActive ? '#eef6ff' : '#9db4d0',
                  textDecoration: 'none',
                  letterSpacing: '0.45px',
                  background: 'transparent',
                  border: 'none',
                  padding: '10px 12px',
                  outline: 'none',
                  whiteSpace: 'nowrap',
                  fontFamily: '"Elms Sans", sans-serif',
                  WebkitFontSmoothing: 'antialiased',
                  MozOsxFontSmoothing: 'grayscale',
                  transform: isActive ? 'translateY(-1.5px)' : 'translateY(0)',
                  textShadow: isActive 
                    ? `
                      0 1px 0 rgba(255, 255, 255, 0.15),
                      0 -1px 0 rgba(0, 0, 0, 0.8),
                      1px 1px 0 rgba(255, 255, 255, 0.08),
                      -1px 1px 0 rgba(255, 255, 255, 0.06)
                    `
                    : `
                      0 1px 0 rgba(255, 255, 255, 0.08),
                      0 -1px 0 rgba(0, 0, 0, 0.65),
                      1px 1px 0 rgba(255, 255, 255, 0.05),
                      -1px 1px 0 rgba(255, 255, 255, 0.04)
                    `,
                }}
                onMouseEnter={(e) => {
                  if (!isActive) {
                    e.currentTarget.style.color = '#d7e8ff'
                    e.currentTarget.style.transform = 'translateY(-0.5px)'
                    e.currentTarget.style.textShadow = `
                      0 1px 0 rgba(255, 255, 255, 0.12),
                      0 -1px 0 rgba(0, 0, 0, 0.72),
                      1px 1px 0 rgba(255, 255, 255, 0.06),
                      -1px 1px 0 rgba(255, 255, 255, 0.05)
                    `
                  }
                }}
                onMouseLeave={(e) => {
                  if (!isActive) {
                    e.currentTarget.style.color = '#9db4d0'
                    e.currentTarget.style.transform = 'translateY(0)'
                    e.currentTarget.style.textShadow = `
                      0 1px 0 rgba(255, 255, 255, 0.08),
                      0 -1px 0 rgba(0, 0, 0, 0.65),
                      1px 1px 0 rgba(255, 255, 255, 0.05),
                      -1px 1px 0 rgba(255, 255, 255, 0.04)
                    `
                  }
                }}
              >
                {item.label}
              </motion.button>
            )
          })}
        </div>
      </div>
    </motion.nav>
  )
}
