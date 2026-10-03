'use client'

import { AuroraBackground } from '@/components/ui/aurora-background'

export default function PaperBackground() {
  return (
    <div className="w-full h-full fixed inset-0 -z-10 overflow-hidden">
      <AuroraBackground showRadialGradient={true}>
        <div className="absolute inset-0" />
      </AuroraBackground>
    </div>
  )
}
