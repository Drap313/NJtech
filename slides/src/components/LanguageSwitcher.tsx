import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Popover, PopoverTrigger, PopoverContent } from '@/components/ui/popover';
import { useLanguage } from '@/contexts/LanguageContext';

type Language = 'en' | 'es' | 'fr' | 'de' | 'zh' | 'ja';

interface LanguageOption {
  code: Language;
  name: string;
  flag: string;
}

const languages: LanguageOption[] = [
  { code: 'en', name: 'English', flag: '🇺🇸' },
  { code: 'es', name: 'Español', flag: '🇪🇸' },
  { code: 'fr', name: 'Français', flag: '🇫🇷' },
  { code: 'de', name: 'Deutsch', flag: '🇩🇪' },
  { code: 'zh', name: '中文', flag: '🇨🇳' },
  { code: 'ja', name: '日本語', flag: '🇯🇵' },
];

export const LanguageSwitcher: React.FC = () => {
  const { language, setLanguage } = useLanguage();
  const [isOpen, setIsOpen] = useState(false);
  const currentLang = languages.find(lang => lang.code === language) || languages[0];

  return (
    <Popover open={isOpen} onOpenChange={setIsOpen}>
      <PopoverTrigger asChild>
        <motion.button
          className="fixed bottom-4 right-4 md:bottom-8 md:right-8 z-50 rounded-full"
          style={{
            width: '56px',
            height: '56px',
            background: `
              linear-gradient(135deg, 
                #08110d 0%, 
                #0d1712 15%, 
                #122019 30%, 
                #162922 45%, 
                #19322a 60%, 
                #1d3a31 75%, 
                #214339 90%, 
                #183026 100%
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
            border: 'none',
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: '24px',
            overflow: 'hidden',
          }}
          whileHover={{ scale: 1.05 }}
          whileTap={{ scale: 0.95 }}
        >
          {/* Top edge ridge */}
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
          
          {/* Directional light */}
          <div 
            className="absolute inset-0 rounded-full pointer-events-none"
            style={{
              background: 'linear-gradient(135deg, rgba(255, 255, 255, 0.06) 0%, rgba(255, 255, 255, 0.03) 20%, rgba(255, 255, 255, 0.01) 40%, rgba(255, 255, 255, 0) 65%)',
            }}
          />
          
          {/* Gloss reflection */}
          <div 
            className="absolute rounded-full pointer-events-none"
            style={{
              left: '18%',
              top: '16%',
              width: '24px',
              height: '7px',
              background: 'radial-gradient(ellipse at center, rgba(255, 255, 255, 0.12) 0%, rgba(255, 255, 255, 0.06) 40%, rgba(255, 255, 255, 0.02) 70%, rgba(255, 255, 255, 0) 100%)',
              filter: 'blur(4px)',
              transform: 'rotate(-12deg)',
            }}
          />
          
          {/* Bottom shadow */}
          <div 
            className="absolute inset-x-0 bottom-0 rounded-b-full pointer-events-none"
            style={{
              height: '50%',
              background: 'linear-gradient(0deg, rgba(0, 0, 0, 0.5) 0%, rgba(0, 0, 0, 0.3) 25%, rgba(0, 0, 0, 0.15) 50%, rgba(0, 0, 0, 0) 100%)',
            }}
          />
          
          {/* Inner glow */}
          <div 
            className="absolute inset-0 rounded-full pointer-events-none"
            style={{
              boxShadow: 'inset 0 0 20px rgba(62, 207, 142, 0.05)',
            }}
          />
          
          {/* Edge definition */}
          <div 
            className="absolute inset-0 rounded-full pointer-events-none"
            style={{
              boxShadow: 'inset 0 0 0 0.5px rgba(255, 255, 255, 0.12)',
            }}
          />
          
          {/* Flag emoji */}
          <span className="relative z-10">{currentLang.flag}</span>
        </motion.button>
      </PopoverTrigger>
      <PopoverContent 
        side="top" 
        align="end"
        sideOffset={8}
        className="w-[calc(100vw-2rem)] max-w-56 p-2 bg-popover border-border mb-2"
        style={{
          background: `
            linear-gradient(135deg, 
              #08110d 0%, 
              #0d1712 15%, 
              #122019 30%, 
              #162922 45%, 
              #19322a 60%, 
              #1d3a31 75%, 
              #214339 90%, 
              #183026 100%
            )
          `,
          boxShadow: `
            0 2px 4px rgba(0, 0, 0, 0.4),
            0 6px 12px rgba(0, 0, 0, 0.5),
            0 12px 24px rgba(0, 0, 0, 0.6),
            inset 0 1px 1px rgba(255, 255, 255, 0.08)
          `,
          border: '1px solid rgba(62, 207, 142, 0.16)',
        }}
      >
        <div className="space-y-1">
          {languages.map((lang) => (
            <motion.button
              key={lang.code}
              onClick={() => {
                setLanguage(lang.code);
                setIsOpen(false);
              }}
              className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-left transition-colors ${
                language === lang.code
                  ? 'bg-primary/20 text-foreground'
                  : 'text-muted-foreground hover:bg-accent/10 hover:text-foreground'
              }`}
              whileHover={{ x: 2 }}
              whileTap={{ scale: 0.98 }}
              style={{
                fontFamily: '"Elms Sans", sans-serif',
              }}
            >
              <span className="text-2xl">{lang.flag}</span>
              <span className="font-medium">{lang.name}</span>
              {language === lang.code && (
                <motion.span
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  className="ml-auto text-primary"
                >
                  ✓
                </motion.span>
              )}
            </motion.button>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );
};
