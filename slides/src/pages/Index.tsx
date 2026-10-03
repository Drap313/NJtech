import { useEffect, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import { Section } from '@/components/Section';
import { PillBase } from '@/components/ui/3d-adaptive-navigation-bar';
import PaperBackground from '@/components/PaperBackground';
import { LinesPatternCard, LinesPatternCardBody } from '@/components/ui/card-with-lines-pattern';
import { useLanguage } from '@/contexts/LanguageContext';

const dispatchCounters = [
  { value: '1', label: 'dispatcher' },
  { value: '40', label: 'trucks' },
  { value: '40', label: 'drivers' },
];

const dispatchChips = [
  'Active loads',
  'Upcoming loads',
  'Deadlines',
  'Routes',
  'Driver status',
  'Legal limits',
  'Locations',
  'ETAs',
];

const industryCards = [
  {
    number: '$906B',
    text: 'US trucking revenue in a year — roughly three quarters of everything the country moves by freight.',
    citation: '(American Trucking Associations, 2024).',
    numberClassName: 'text-[#5fd6e4]',
    borderClassName: 'border-[#5fd6e4]/25',
    shadowClassName: 'shadow-[0_30px_80px_rgba(95,214,228,0.12)]',
  },
  {
    number: '$108.8B',
    text: 'Lost to delay every year — $7,588 of it per truck, before a single fine or claim.',
    citation: '(ATRI Cost of Congestion; record high, +15% year over year).',
    numberClassName: 'text-destructive',
    borderClassName: 'border-destructive/25',
    shadowClassName: 'shadow-[0_30px_80px_rgba(255,79,79,0.10)]',
  },
];

const engineLayers = [
  {
    index: '01',
    title: 'Deterministic layer',
    tech: ['Python 3.11 · zero deps', '49 CFR § 395.3'],
    text: 'Maximum driving time for property-carrying vehicles — 11-hour drive, 14-hour window, 30-minute break, 34-hour reset, 60/70 cycle. Anything unsupported returns manual_review, never a guess.',
    accentClassName: 'text-primary',
    borderClassName: 'border-primary/30',
    bgClassName: 'bg-primary/10',
  },
  {
    index: '02',
    title: 'Local model',
    tech: ['vLLM · Qwen3.6-35B-A3B NVFP4', 'Dell Pro Max GB10'],
    text: 'Explains what broke, compares the recovery options, drafts the dispatcher message. Provisioned by NemoClaw. It never decides what is legal.',
    accentClassName: 'text-secondary',
    borderClassName: 'border-secondary/30',
    bgClassName: 'bg-secondary/10',
  },
  {
    index: '03',
    title: 'Agent and surface',
    tech: ['NemoClaw / OpenClaw', 'OpenShell', 'Slack Block Kit'],
    text: 'A typed tool-calling loop under an OpenShell egress policy, approvals in Slack, and an append-only JSONL audit trail.',
    accentClassName: 'text-accent',
    borderClassName: 'border-accent/30',
    bgClassName: 'bg-accent/10',
  },
];

const comparisonColumns = ['ELD / Telematics', 'Enterprise TMS', 'Dispatch Guardian'];

const comparisonRows: { capability: string; values: ('yes' | 'no' | 'partial' | string)[] }[] = [
  { capability: 'Logs hours of service', values: ['yes', 'no', 'yes'] },
  { capability: 'Builds the plan up front', values: ['no', 'yes', 'yes'] },
  { capability: 'Re-checks it when reality changes', values: ['no', 'no', 'yes'] },
  { capability: 'Ranks recovery options by cost', values: ['no', 'no', 'yes'] },
  { capability: 'Acts before the violation', values: ['after the fact', 'no', 'yes'] },
  { capability: 'Runs on your own hardware', values: ['no', 'no', 'yes'] },
  { capability: 'Named approver + audit trail', values: ['no', 'partial', 'yes'] },
];

const ComparisonCell = ({ value, highlight }: { value: string; highlight?: boolean }) => {
  if (value === 'yes') {
    return <span className={`text-xl font-bold ${highlight ? 'text-primary' : 'text-foreground'}`}>✓</span>;
  }
  if (value === 'no') {
    return <span className="text-xl font-bold text-muted-foreground/40">—</span>;
  }
  if (value === 'partial') {
    return <span className="text-sm text-muted-foreground">partial</span>;
  }
  return <span className="text-xs md:text-sm text-muted-foreground">{value}</span>;
};

const Citation = ({ text, className = '' }: { text: string; className?: string }) => (
  <p className={`mt-4 text-xs leading-relaxed tracking-wide text-muted-foreground/70 ${className}`}>
    {text}
  </p>
);

/**
 * Shows the image at `src` once that file exists in public/.
 * Until then (or if it fails to load) it renders a labelled placeholder
 * naming the exact filename to drop in — no code change needed.
 */
const ScreenshotSlot = ({
  label,
  src,
  caption,
  className = '',
}: {
  label: string;
  src: string;
  caption?: string;
  className?: string;
}) => {
  const [failed, setFailed] = useState(false);

  return (
    <div className="flex flex-col gap-2">
      <div
        className={`relative overflow-hidden rounded-xl border bg-card/60 ${
          failed ? 'border-2 border-dashed border-primary/30' : 'border-border shadow-2xl'
        } ${className}`}
      >
        {failed ? (
          <div className="flex h-full w-full flex-col items-center justify-center gap-2 p-5 text-center">
            <span className="rounded-full border border-primary/30 bg-primary/10 px-3 py-1 text-[0.6rem] font-mono uppercase tracking-[0.2em] text-primary">
              screenshot
            </span>
            <p className="text-sm text-muted-foreground">{label}</p>
            <code className="text-[0.65rem] text-muted-foreground/60">public{src}</code>
          </div>
        ) : (
          <img
            src={src}
            alt={label}
            onError={() => setFailed(true)}
            className="h-full w-full object-contain"
          />
        )}
      </div>
      {caption && (
        <p className="text-center text-xs md:text-sm text-muted-foreground">{caption}</p>
      )}
    </div>
  );
};

const MAIN_NAV_ITEMS = [
  { label: 'Home', id: 'home' },
  { label: 'Dispatch', id: 'dispatch' },
  { label: 'Industry', id: 'industry' },
  { label: 'Impact', id: 'impact' },
  { label: 'Guardian', id: 'guardian' },
  { label: 'Demo', id: 'demo' },
  { label: 'Slack', id: 'approval' },
  { label: 'Engine', id: 'engine' },
  { label: 'Compare', id: 'compare' },
  { label: 'Close', id: 'conclusion' },
];

const SECTION_IDS = MAIN_NAV_ITEMS.map((item) => item.id);

const Index = () => {
  const { t } = useLanguage();
  const [activeSection, setActiveSection] = useState('home');
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const activeSectionRef = useRef(activeSection);
  activeSectionRef.current = activeSection;

  const teamMembers = [
    { name: 'Max Martinez', role: 'Mechanical Engineering', initials: 'M', image: '/Max_Headshot.webp', school: 'New Jersey Institute of Technology' },
    { name: 'Yahil Corcino', role: 'Computer Engineering', initials: 'Y', image: '/Yahil_Headshot.webp', school: 'New Jersey Institute of Technology' },
    { name: 'David Rapozo', role: 'Computer Engineering', initials: 'DR', image: '/David_Headshot.jpeg', school: 'New Jersey Institute of Technology' },
  ];

  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!container) return;

    const handleScroll = () => {
      const scrollTop = container.scrollTop;
      const viewportH = container.clientHeight;
      const scrollMid = scrollTop + viewportH / 2;

      for (const sectionId of SECTION_IDS) {
        const el = document.getElementById(sectionId);
        if (el) {
          const top = el.offsetTop;
          if (scrollMid >= top && scrollMid < top + el.offsetHeight) {
            setActiveSection(sectionId);
            break;
          }
        }
      }
    };

    container.addEventListener('scroll', handleScroll, { passive: true });
    handleScroll();
    return () => container.removeEventListener('scroll', handleScroll);
  }, []);

  const scrollToSection = (sectionId: string) => {
    const element = document.getElementById(sectionId);
    if (element) {
      element.scrollIntoView({ behavior: 'smooth' });
    }
  };

  // Keyboard / presentation-clicker navigation between slides.
  useEffect(() => {
    const NEXT_KEYS = ['ArrowRight', 'ArrowDown', 'PageDown', ' ', 'Spacebar'];
    const PREV_KEYS = ['ArrowLeft', 'ArrowUp', 'PageUp'];

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return;

      const target = event.target as HTMLElement | null;
      if (target?.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target?.tagName ?? '')) {
        return;
      }

      const isNext = NEXT_KEYS.includes(event.key);
      const isPrev = PREV_KEYS.includes(event.key);
      const isFirst = event.key === 'Home';
      const isLast = event.key === 'End';
      if (!isNext && !isPrev && !isFirst && !isLast) return;

      // Resolve the current slide from scroll position so a held key can't
      // outrun the scroll listener and skip a slide.
      const container = scrollContainerRef.current;
      let currentIndex = SECTION_IDS.indexOf(activeSectionRef.current);
      if (container) {
        const mid = container.scrollTop + container.clientHeight / 2;
        const atMid = SECTION_IDS.findIndex((id) => {
          const el = document.getElementById(id);
          return el ? mid >= el.offsetTop && mid < el.offsetTop + el.offsetHeight : false;
        });
        if (atMid !== -1) currentIndex = atMid;
      }
      if (currentIndex === -1) currentIndex = 0;

      let nextIndex = currentIndex;
      if (isNext) nextIndex = Math.min(currentIndex + 1, SECTION_IDS.length - 1);
      if (isPrev) nextIndex = Math.max(currentIndex - 1, 0);
      if (isFirst) nextIndex = 0;
      if (isLast) nextIndex = SECTION_IDS.length - 1;

      event.preventDefault();
      if (nextIndex !== currentIndex) scrollToSection(SECTION_IDS[nextIndex]);
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  return (
    <div className="relative min-h-screen">
      <PaperBackground />

      <div className="fixed top-8 left-1/2 z-50 -translate-x-1/2">
        <PillBase activeSection={activeSection} navItems={MAIN_NAV_ITEMS} onSectionClick={scrollToSection} />
      </div>

      <div ref={scrollContainerRef} className="snap-y snap-mandatory h-screen overflow-y-scroll scrollbar-hide relative">
        <Section id="home" className="bg-transparent" contentClassName="max-w-7xl py-16 lg:py-20">
          <div className="space-y-12">
            <motion.div
              initial={{ opacity: 0, y: 30 }}
              whileInView={{ opacity: 1, y: 0 }}
              transition={{ duration: 1, ease: [0.22, 1, 0.36, 1] }}
              className="mx-auto max-w-6xl text-center"
            >
              <h1 className="text-5xl md:text-7xl font-black tracking-tight text-foreground">
                Dispatch <span className="text-primary">Guardian</span>
              </h1>
            </motion.div>

            <div className="grid gap-8 md:grid-cols-3 max-w-5xl mx-auto mt-14">
              {teamMembers.map((member, index) => (
                <motion.div
                  key={member.name}
                  initial={{ opacity: 0, y: 50 }}
                  whileInView={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.8, delay: 0.2 + index * 0.15 }}
                >
                  <LinesPatternCard
                    className="h-[24rem] rounded-[2rem] shadow-2xl"
                    patternClassName="h-full overflow-hidden rounded-[1.25rem]"
                    gradientClassName="h-full overflow-hidden rounded-[1.25rem]"
                  >
                    <LinesPatternCardBody className="h-full rounded-[1.25rem] bg-gradient-to-br from-primary/10 to-secondary/5 p-0 md:p-0">
                      <div className="flex h-full flex-col items-center px-5 py-8 text-center sm:px-6">
                        {member.image ? (
                          <div className="flex h-36 items-center justify-center">
                            <div className="h-32 w-32 overflow-hidden rounded-full border border-primary/20 shadow-sm">
                              <img
                                src={member.image}
                                alt={member.name}
                                className="h-full w-full object-cover"
                              />
                            </div>
                          </div>
                        ) : (
                          <div className="flex h-36 items-center justify-center">
                            <div className="flex h-32 w-32 items-center justify-center rounded-full border border-primary/20 bg-background/80 text-3xl font-bold text-primary shadow-sm">
                              {member.initials}
                            </div>
                          </div>
                        )}
                        <div className="mt-6 flex min-h-[7.5rem] w-full flex-col items-center">
                          <p className="text-xl leading-tight text-foreground font-semibold">{member.name}</p>
                          <p className="mt-3 w-full text-[0.95rem] leading-tight text-muted-foreground">{member.role}</p>
                          <p className="mt-2 text-sm font-bold tracking-wide text-foreground">
                            {member.school}
                          </p>
                        </div>
                      </div>
                    </LinesPatternCardBody>
                  </LinesPatternCard>
                </motion.div>
              ))}
            </div>

            <motion.p
              initial={{ opacity: 0, y: 24 }}
              whileInView={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.8, delay: 0.15, ease: [0.22, 1, 0.36, 1] }}
              className="mx-auto mt-10 max-w-4xl text-balance text-center text-xl font-light leading-snug text-muted-foreground md:text-2xl xl:text-[2rem]"
            >
              {t('home.subtitle')}
            </motion.p>
          </div>
        </Section>

        <Section id="dispatch" className="bg-transparent" contentClassName="max-w-6xl py-8">
          <div className="space-y-8 text-center">
            <motion.h2
              initial={{ opacity: 0, y: 24 }}
              whileInView={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1] }}
              viewport={{ once: false, amount: 0.35 }}
              className="text-3xl font-black tracking-tight leading-[1.05] text-foreground md:text-5xl xl:text-6xl"
            >
              One dispatcher. <span className="text-primary">Forty trucks.</span>
            </motion.h2>

            <div className="grid gap-5 sm:grid-cols-3 max-w-4xl mx-auto">
              {dispatchCounters.map((counter, index) => (
                <motion.div
                  key={counter.label}
                  initial={{ opacity: 0, y: 24 }}
                  whileInView={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.5, delay: 0.1 * index }}
                  viewport={{ once: false, amount: 0.3 }}
                  className="rounded-[1.5rem] border border-primary/25 bg-card/90 px-6 py-7 backdrop-blur-md"
                >
                  <div className="text-5xl md:text-6xl font-black tracking-tight text-primary">
                    {counter.value}
                  </div>
                  <p className="mt-2 text-base md:text-lg text-muted-foreground">{counter.label}</p>
                </motion.div>
              ))}
            </div>

            <div className="flex flex-wrap items-center justify-center gap-2 max-w-4xl mx-auto">
              {dispatchChips.map((chip) => (
                <span
                  key={chip}
                  className="rounded-full border border-border bg-card/70 px-3 py-1 text-xs md:text-sm text-muted-foreground"
                >
                  {chip}
                </span>
              ))}
            </div>

            <p className="mx-auto max-w-4xl text-base md:text-lg text-muted-foreground">
              Any of them can change at any hour of the day.
            </p>

            <p className="mx-auto max-w-4xl text-lg md:text-xl text-foreground">
              Held together in someone&rsquo;s head, on paper, or in outdated software —
              <span className="text-destructive"> and every handoff is a chance for human error</span>.
            </p>
          </div>
        </Section>

        <Section id="industry" className="bg-transparent" contentClassName="max-w-6xl py-8">
          <div className="space-y-7">
            <motion.h2
              initial={{ opacity: 0, y: 24 }}
              whileInView={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1] }}
              viewport={{ once: false, amount: 0.35 }}
              className="mx-auto max-w-5xl text-center text-3xl font-black tracking-tight leading-[1.05] text-foreground md:text-5xl xl:text-6xl"
            >
              Enormous industry. <span className="text-destructive">Enormous leakage.</span>
            </motion.h2>

            <div className="grid max-w-5xl mx-auto gap-5 md:grid-cols-2">
              {industryCards.map((card, index) => (
                <motion.div
                  key={card.number}
                  initial={{ opacity: 0, y: 28 }}
                  whileInView={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.5, delay: 0.08 * index }}
                  viewport={{ once: false, amount: 0.3 }}
                  className={`h-full rounded-[1.5rem] border bg-card/90 px-6 py-6 backdrop-blur-md ${card.borderClassName} ${card.shadowClassName}`}
                >
                  <div className={`text-4xl font-black tracking-tight md:text-5xl ${card.numberClassName}`}>
                    {card.number}
                  </div>
                  <p className="mt-3 text-sm leading-relaxed text-muted-foreground md:text-base">
                    {card.text}
                  </p>
                  <Citation text={card.citation} className="!mt-2" />
                </motion.div>
              ))}
            </div>

            <p className="text-center text-base md:text-lg text-muted-foreground max-w-4xl mx-auto">
              Another <span className="text-foreground">$11.5B</span> goes to detention at the dock.
            </p>
          </div>
        </Section>

        <Section id="impact" className="bg-transparent" contentClassName="max-w-6xl py-8">
          <div className="space-y-7">
            <motion.h2
              initial={{ opacity: 0, y: 24 }}
              whileInView={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1] }}
              viewport={{ once: false, amount: 0.35 }}
              className="mx-auto max-w-5xl text-center text-3xl font-black tracking-tight leading-[1.05] text-foreground md:text-5xl xl:text-6xl"
            >
              Then one of them <span className="text-destructive">runs late</span>.
            </motion.h2>

            <div className="grid gap-5 md:grid-cols-2 max-w-5xl mx-auto">
              <motion.div
                initial={{ opacity: 0, y: 24 }}
                whileInView={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.5 }}
                viewport={{ once: false, amount: 0.3 }}
                className="rounded-[1.5rem] border border-destructive/30 bg-destructive/5 p-6"
              >
                <p className="text-sm font-semibold uppercase tracking-[0.16em] text-destructive">Push through</p>
                <p className="mt-2 text-2xl md:text-3xl font-black text-destructive">Over the legal limit</p>
                <p className="mt-3 text-base text-muted-foreground leading-relaxed">
                  A violation, CSA points, and a driver on the road who should not be.
                </p>
              </motion.div>

              <motion.div
                initial={{ opacity: 0, y: 24 }}
                whileInView={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.5, delay: 0.12 }}
                viewport={{ once: false, amount: 0.3 }}
                className="rounded-[1.5rem] border border-[#e4b24d]/30 bg-[#e4b24d]/5 p-6"
              >
                <p className="text-sm font-semibold uppercase tracking-[0.16em] text-[#e4b24d]">Stop</p>
                <p className="mt-2 text-2xl md:text-3xl font-black text-[#e4b24d]">Late delivery</p>
                <p className="mt-3 text-base text-muted-foreground leading-relaxed">
                  Late fees, a missed window, and a shipper who remembers next quarter.
                </p>
              </motion.div>
            </div>

            <p className="text-center text-base md:text-lg text-muted-foreground max-w-4xl mx-auto">
              Detention alone burns <span className="text-foreground">117-209 hours</span> of a driver&rsquo;s
              legal clock a year. And if it ends in a crash, the average claim is{' '}
              <span className="text-foreground">$450,000</span>.
            </p>
          </div>
        </Section>

        <Section id="guardian" className="bg-transparent" contentClassName="max-w-6xl py-8">
          <div className="space-y-8 text-center">
            <motion.div
              initial={{ opacity: 0, y: 24 }}
              whileInView={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1] }}
              viewport={{ once: false, amount: 0.35 }}
            >
              <h2 className="text-3xl font-black tracking-tight leading-[1.05] text-foreground md:text-5xl xl:text-6xl">
                Dispatch Guardian. <span className="text-primary">One view.</span>
              </h2>
              <p className="mx-auto mt-4 max-w-3xl text-lg md:text-xl text-muted-foreground">
                Every driver, truck, load, and clock in the same place.
              </p>
            </motion.div>

            <div className="flex flex-wrap items-center justify-center gap-2.5 max-w-5xl mx-auto">
              {dispatchChips.concat(['Hours worked', 'Legal limits']).map((chip, index) => (
                <motion.span
                  key={chip}
                  initial={{ opacity: 0, scale: 0.9 }}
                  whileInView={{ opacity: 1, scale: 1 }}
                  transition={{ duration: 0.35, delay: 0.04 * index }}
                  viewport={{ once: false, amount: 0.3 }}
                  className="rounded-full border border-primary/30 bg-primary/10 px-4 py-1.5 text-sm md:text-base text-foreground"
                >
                  {chip}
                </motion.span>
              ))}
            </div>

            <p className="mx-auto max-w-4xl text-lg md:text-xl text-foreground">
              It doesn&rsquo;t just help build the plan. It helps you{' '}
              <span className="text-primary">respond when the plan breaks</span>.
            </p>
          </div>
        </Section>

        <Section id="demo" className="bg-transparent" contentClassName="max-w-7xl py-5">
          <div className="space-y-4">
            <h2 className="text-center text-4xl font-black tracking-tight text-foreground md:text-6xl">
              Demo
            </h2>

            <div className="mx-auto w-full max-w-2xl lg:max-w-3xl">
              <ScreenshotSlot
                label="Overview — the whole operation at once"
                src="/demo-overlay.png"
                className="aspect-video"
              />
            </div>

            <div className="grid gap-4 md:grid-cols-2 max-w-5xl mx-auto">
              <ScreenshotSlot
                label="Drivers"
                src="/demo-drivers.png"
                caption="Rosters, hours worked, legal limits"
                className="aspect-video"
              />
              <ScreenshotSlot
                label="Loads"
                src="/demo-loads.png"
                caption="Active, upcoming, and their deadlines"
                className="aspect-video"
              />
            </div>
          </div>
        </Section>

        <Section id="approval" className="bg-transparent" contentClassName="max-w-7xl py-5">
          <div className="space-y-4">
            <h2 className="text-center text-4xl font-black tracking-tight text-foreground md:text-6xl">
              Demo
            </h2>

            <div className="mx-auto grid w-full max-w-6xl items-center gap-5 lg:grid-cols-[2.5fr_1fr]">
              <div className="space-y-4">
                <ScreenshotSlot
                  label="Schedule — every driver's projected day, and the incident on it"
                  src="/demo-schedule.png"
                  className="aspect-video"
                />
                <div className="grid gap-4 grid-cols-2">
                  <ScreenshotSlot
                    label="Incident detail"
                    src="/demo-incident.png"
                    caption="41 min over the 14-hour window"
                    className="aspect-video"
                  />
                  <ScreenshotSlot
                    label="Options compared"
                    src="/demo-options.png"
                    caption="Relay $286 · swap $411 · hold $1,200"
                    className="aspect-video"
                  />
                </div>
              </div>

              <ScreenshotSlot
                label="Slack approval"
                src="/demo-slack.png"
                caption="Approved from a phone"
                className="mx-auto h-[20rem] lg:h-[26rem] aspect-[1320/2868]"
              />
            </div>
          </div>
        </Section>

        <Section id="engine" className="bg-transparent" contentClassName="max-w-6xl py-8">
          <div className="space-y-7">
            <motion.h2
              initial={{ opacity: 0, y: 24 }}
              whileInView={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1] }}
              viewport={{ once: false, amount: 0.35 }}
              className="mx-auto max-w-5xl text-center text-3xl font-black tracking-tight leading-[1.05] text-foreground md:text-5xl xl:text-6xl"
            >
              Three layers. <span className="text-primary">One decision.</span>
            </motion.h2>

            <div className="flex flex-col gap-3 max-w-5xl mx-auto">
              {engineLayers.map((layer, index) => (
                <motion.div
                  key={layer.title}
                  initial={{ opacity: 0, y: 20 }}
                  whileInView={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.45, delay: 0.1 * index }}
                  viewport={{ once: false, amount: 0.25 }}
                  className={`flex items-start gap-4 rounded-xl border p-4 md:p-5 text-left ${layer.bgClassName} ${layer.borderClassName}`}
                >
                  <span className={`font-mono text-sm font-bold ${layer.accentClassName}`}>{layer.index}</span>
                  <div className="flex-1">
                    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1.5">
                      <p className={`text-xl md:text-2xl font-semibold ${layer.accentClassName}`}>{layer.title}</p>
                      {layer.tech.map((tag) => (
                        <span
                          key={tag}
                          className="rounded border border-border bg-background/40 px-2 py-0.5 font-mono text-[0.65rem] text-muted-foreground"
                        >
                          {tag}
                        </span>
                      ))}
                    </div>
                    <p className="mt-2 text-sm md:text-base leading-relaxed text-muted-foreground">{layer.text}</p>
                  </div>
                </motion.div>
              ))}
            </div>

            <div className="rounded-xl border border-primary/30 bg-primary/5 p-5 max-w-5xl mx-auto text-center">
              <p className="text-lg md:text-xl text-foreground">
                The dispatcher still decides — in{' '}
                <span className="font-semibold text-primary">one to five minutes</span>, not thirty.
              </p>
              <p className="mt-2 text-sm md:text-base text-muted-foreground">
                The model runs on your box: private, and still running at 3am.
              </p>
            </div>
          </div>
        </Section>

        <Section id="compare" className="bg-transparent" contentClassName="max-w-6xl py-6">
          <div className="space-y-4">
            <motion.h2
              initial={{ opacity: 0, y: 24 }}
              whileInView={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1] }}
              viewport={{ once: false, amount: 0.35 }}
              className="text-center text-3xl font-black tracking-tight leading-[1.05] text-foreground md:text-5xl"
            >
              Everyone solved <span className="text-primary">one piece</span>.
            </motion.h2>

            <div className="mx-auto max-w-5xl overflow-hidden rounded-2xl border border-border bg-card/80 backdrop-blur-md">
              {/* header */}
              <div className="grid grid-cols-[1.5fr_1fr_1fr_1.15fr] items-end gap-2 border-b border-border px-4 py-3">
                <div />
                {comparisonColumns.map((col, i) => (
                  <div
                    key={col}
                    className={`text-center text-xs md:text-sm font-semibold leading-tight ${
                      i === 2 ? 'text-primary' : 'text-muted-foreground'
                    }`}
                  >
                    {col}
                  </div>
                ))}
              </div>

              {/* capability checklist */}
              {comparisonRows.map((row, rowIndex) => (
                <motion.div
                  key={row.capability}
                  initial={{ opacity: 0, x: -12 }}
                  whileInView={{ opacity: 1, x: 0 }}
                  transition={{ duration: 0.35, delay: 0.05 * rowIndex }}
                  viewport={{ once: false, amount: 0.3 }}
                  className={`grid grid-cols-[1.5fr_1fr_1fr_1.15fr] items-center gap-2 px-5 py-3 ${
                    rowIndex === comparisonRows.length - 1 ? '' : 'border-b border-border/40'
                  }`}
                >
                  <div className="text-sm md:text-base text-foreground">{row.capability}</div>
                  {row.values.map((value, i) => (
                    <div
                      key={i}
                      className={`flex items-center justify-center text-center ${
                        i === 2 ? 'rounded-md bg-primary/10 py-1.5' : ''
                      }`}
                    >
                      <ComparisonCell value={value} highlight={i === 2} />
                    </div>
                  ))}
                </motion.div>
              ))}

            </div>

            <p className="mx-auto max-w-3xl text-center text-lg md:text-xl font-medium text-foreground">
              All of them assume <span className="text-destructive">the plan holds</span>.
            </p>
          </div>
        </Section>

        <Section id="conclusion" className="bg-transparent" contentClassName="max-w-5xl py-16">
          <div className="space-y-10 text-center">
            <motion.div
              initial={{ opacity: 0, y: 30 }}
              whileInView={{ opacity: 1, y: 0 }}
              transition={{ duration: 1, ease: [0.22, 1, 0.36, 1] }}
              viewport={{ once: false, amount: 0.3 }}
            >
              <h1 className="text-6xl md:text-8xl font-black tracking-tight text-foreground">
                Thank you
              </h1>
              <p className="mt-6 text-2xl md:text-3xl font-semibold text-foreground">
                Dispatch <span className="text-primary">Guardian</span>
              </p>
            </motion.div>

            <motion.div
              initial={{ opacity: 0, y: 24 }}
              whileInView={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.8, delay: 0.2, ease: [0.22, 1, 0.36, 1] }}
              viewport={{ once: false, amount: 0.3 }}
              className="space-y-3"
            >
              <p className="text-xl md:text-2xl text-muted-foreground">
                {teamMembers.map((member) => member.name).join('  ·  ')}
              </p>
              <p className="text-base md:text-lg font-semibold tracking-wide text-foreground">
                New Jersey Institute of Technology
              </p>
            </motion.div>

            <motion.div
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.8, delay: 0.35, ease: [0.22, 1, 0.36, 1] }}
              viewport={{ once: false, amount: 0.3 }}
              className="space-y-5"
            >
              <p className="mx-auto max-w-3xl text-2xl md:text-3xl font-medium leading-snug text-foreground">
                &ldquo;Trucks are the <span className="text-primary">traces of the physical world</span>.&rdquo;
              </p>
              <p className="text-lg md:text-xl text-muted-foreground">
                Deterministic rules. Local reasoning.{' '}
                <span className="text-primary">A human in the loop.</span>
              </p>
            </motion.div>
          </div>
        </Section>
      </div>

    </div>
  );
};

export default Index;
