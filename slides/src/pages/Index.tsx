import { useEffect, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import { Section } from '@/components/Section';
import { PillBase } from '@/components/ui/3d-adaptive-navigation-bar';
import PaperBackground from '@/components/PaperBackground';
import { LinesPatternCard, LinesPatternCardBody } from '@/components/ui/card-with-lines-pattern';
import { useLanguage } from '@/contexts/LanguageContext';

const stakesCards = [
  {
    number: '$450K',
    text: 'Average payout when an ordinary truck crash becomes a claim. Not the headline verdict — the common case, and the one a small fleet actually meets.',
    citation: '(ATRI, 600+ cases settled under $1M, 2021).',
    numberClassName: 'text-[#5fd6e4]',
    borderClassName: 'border-[#5fd6e4]/25',
    shadowClassName: 'shadow-[0_30px_80px_rgba(95,214,228,0.12)]',
  },
  {
    number: '$3.6M',
    text: 'Median verdict once a case crosses $1M in state court. An hours-of-service violation is what moves a case from the first number to this one.',
    citation: '(ATRI Trucking Litigation: A Forensic Analysis, 2025).',
    numberClassName: 'text-[#e4b24d]',
    borderClassName: 'border-[#e4b24d]/25',
    shadowClassName: 'shadow-[0_30px_80px_rgba(228,178,77,0.12)]',
  },
  {
    number: '$10M',
    text: 'Fatal-crash verdict that ended a 115-truck carrier. It "far exceeded the amount of liability insurance and reserves." Chapter 11, four months later.',
    citation: '(Marvin Keller Trucking, 2022).',
    numberClassName: 'text-destructive',
    borderClassName: 'border-destructive/25',
    shadowClassName: 'shadow-[0_30px_80px_rgba(255,79,79,0.10)]',
  },
];

const ingestChips = [
  'Driver roster',
  'Hours worked',
  'Legal limits',
  'Active loads',
  'Planned loads',
  'Driver status',
  'Truck location',
  'ETAs',
];

const workflowSteps = [
  { title: 'Fleet Event', borderClassName: 'border-primary/30', arrowClassName: 'text-primary' },
  { title: 'Rule Check', borderClassName: 'border-secondary/30', arrowClassName: 'text-secondary' },
  { title: 'Recovery Solver', borderClassName: 'border-accent/30', arrowClassName: 'text-accent' },
  { title: 'Slack Approval', borderClassName: 'border-destructive/30', arrowClassName: 'text-destructive' },
];

const workflowArtifacts = [
  {
    title: 'Rule Trace',
    text: 'The exact constraint that failed — drive clock, duty window, cycle, break.',
    citation: '(Compliance engine, 49 CFR 395.3 ruleset).',
    accentClassName: 'text-primary',
    borderClassName: 'border-primary/30',
    bgClassName: 'bg-primary/10',
  },
  {
    title: 'Costed Alternatives',
    text: 'Ranked legal options — swap driver, delay, split — each with ETA and cost delta.',
    citation: '(Recovery solver + cost model).',
    accentClassName: 'text-secondary',
    borderClassName: 'border-secondary/30',
    bgClassName: 'bg-secondary/10',
  },
  {
    title: 'Incident Lifecycle',
    text: 'A delay or stale feed opens an incident and re-evaluates the assignment as reality changes.',
    citation: '(Event-driven — always on, not chat-driven).',
    accentClassName: 'text-accent',
    borderClassName: 'border-accent/30',
    bgClassName: 'bg-accent/10',
  },
  {
    title: 'Audit Trail',
    text: 'Every approval and override logged with a named approver. Append-only.',
    citation: '(What an auditor, an underwriter, and a jury ask for first).',
    accentClassName: 'text-destructive',
    borderClassName: 'border-destructive/30',
    bgClassName: 'bg-destructive/10',
  },
];

const stackCards = [
  {
    title: 'Understands the incident',
    text: 'Qwen3.6-35B-A3B (NVFP4) on vLLM, local on the GB10. Reads what broke and says which constraint failed, in plain language.',
    accentClassName: 'text-primary',
    borderClassName: 'border-primary/30',
    bgClassName: 'bg-primary/10',
  },
  {
    title: 'Compares the options',
    text: 'Ranks the legal recoveries against each other by cost and ETA. The dispatcher gets a decision, not another dashboard to read.',
    accentClassName: 'text-secondary',
    borderClassName: 'border-secondary/30',
    bgClassName: 'bg-secondary/10',
  },
  {
    title: 'Extends the dispatcher',
    text: 'One dispatcher covers more loads without losing track of any of them. The bandwidth goes up; the headcount does not.',
    accentClassName: 'text-accent',
    borderClassName: 'border-accent/30',
    bgClassName: 'bg-accent/10',
  },
];

const eldCards = [
  {
    title: 'Inflexible',
    text: 'Hardware in every cab, multi-year contracts, one vendor’s roadmap. It logs the rule it was built for and nothing else.',
    accentClassName: 'text-destructive',
    borderClassName: 'border-destructive/30',
    bgClassName: 'bg-destructive/10',
  },
  {
    title: 'Costly',
    text: '$19-25 per truck per month before telematics, plus hardware per vehicle — to be told what already happened.',
    accentClassName: 'text-[#e4b24d]',
    borderClassName: 'border-[#e4b24d]/30',
    bgClassName: 'bg-[#e4b24d]/10',
  },
  {
    title: 'Not local',
    text: 'The log lives in the vendor’s cloud, under the vendor’s terms — and underwriters already read it as a pricing input.',
    accentClassName: 'text-[#5fd6e4]',
    borderClassName: 'border-[#5fd6e4]/30',
    bgClassName: 'bg-[#5fd6e4]/10',
  },
];


const Citation = ({ text, className = '' }: { text: string; className?: string }) => (
  <p className={`mt-4 text-xs leading-relaxed tracking-wide text-muted-foreground/70 ${className}`}>
    {text}
  </p>
);

const MAIN_NAV_ITEMS = [
  { label: 'Home', id: 'home' },
  { label: 'Problem', id: 'problem' },
  { label: 'Stakes', id: 'stakes' },
  { label: 'Guardian', id: 'how-it-works' },
  { label: 'Stack', id: 'stack' },
  { label: 'ELD', id: 'eld' },
  { label: 'Conclusion', id: 'conclusion' },
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

        <Section id="problem" className="bg-transparent" contentClassName="max-w-6xl py-8">
          <div className="space-y-8">
            <motion.div
              initial={{ opacity: 0, y: 24 }}
              whileInView={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1] }}
              viewport={{ once: false, amount: 0.35 }}
              className="mx-auto max-w-6xl text-center"
            >
              <h2 className="text-3xl font-black tracking-tight leading-[1.05] text-foreground md:text-5xl xl:text-6xl">
                The hours are the driver&rsquo;s. <span className="text-destructive">The liability is the company&rsquo;s.</span>
              </h2>
              <p className="mx-auto mt-4 max-w-4xl text-lg leading-relaxed text-muted-foreground md:text-xl">
                11 hours driving, inside a 14-hour window. Past the line the load is illegal and the truck stops.
              </p>
              <p className="mx-auto mt-3 max-w-4xl text-base leading-relaxed text-muted-foreground md:text-lg">
                Today that math lives in a dispatcher&rsquo;s head or on paper. The software that exists is old,
                or connects one piece of the puzzle — and 97% of US carriers run fewer than 20 trucks, too small
                to buy their way out of it.
              </p>
            </motion.div>

            <motion.div
              initial={{ opacity: 0, y: 28 }}
              whileInView={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.55, delay: 0.18 }}
              viewport={{ once: false, amount: 0.25 }}
            >
              <LinesPatternCard className="max-w-5xl mx-auto rounded-[1.75rem] border-primary/30 shadow-[0_30px_90px_rgba(33,117,78,0.22)]">
                <LinesPatternCardBody className="p-5 md:p-6">
                  <div className="grid gap-4 md:grid-cols-2">
                    <div className="rounded-xl border border-destructive/30 bg-destructive/5 p-5">
                      <p className="text-lg font-semibold text-destructive mb-1">Today</p>
                      <p className="text-3xl md:text-4xl font-black text-destructive">The ELD is the evidence</p>
                      <p className="mt-3 text-base text-muted-foreground leading-relaxed">
                        The ELD records the violation — and hands a plaintiff&rsquo;s attorney the exhibit.
                      </p>
                    </div>
                    <div className="rounded-xl border border-primary/30 bg-primary/5 p-5">
                      <p className="text-lg font-semibold text-primary mb-1">With Dispatch Guardian</p>
                      <p className="text-3xl md:text-4xl font-black text-primary">The record is the defense</p>
                      <p className="mt-3 text-base text-muted-foreground leading-relaxed">
                        Refuses the plan that breaks the clock. Logs the legal one a named dispatcher approved.
                      </p>
                    </div>
                  </div>
                </LinesPatternCardBody>
              </LinesPatternCard>
            </motion.div>

            <p className="text-center text-base md:text-lg text-muted-foreground max-w-4xl mx-auto">
              One delay sends two bills — the violation if the driver keeps going, the late fee if he stops.
              <span className="text-foreground"> Somebody has to price that trade-off in minutes.</span>
            </p>
          </div>
        </Section>

        <Section id="stakes" className="bg-transparent" contentClassName="max-w-[92rem] py-6">
          <div className="space-y-6">
            <motion.div
              initial={{ opacity: 0, y: 24 }}
              whileInView={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1] }}
              viewport={{ once: false, amount: 0.35 }}
              className="mx-auto max-w-6xl text-center"
            >
              <h2 className="text-3xl font-black tracking-tight leading-[1.05] text-foreground md:text-5xl xl:text-6xl">
                When the plan fails, <span className="text-destructive">the company pays.</span>
              </h2>
            </motion.div>

            <div className="grid max-w-6xl mx-auto gap-4 md:grid-cols-3">
              {stakesCards.map((card, index) => (
                <motion.div
                  key={card.number}
                  initial={{ opacity: 0, y: 28 }}
                  whileInView={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.5, delay: 0.08 * index }}
                  viewport={{ once: false, amount: 0.3 }}
                  className={`h-full rounded-[1.5rem] border bg-card/90 px-5 py-5 backdrop-blur-md ${card.borderClassName} ${card.shadowClassName}`}
                >
                  <div className={`text-4xl font-black tracking-tight md:text-5xl ${card.numberClassName}`}>
                    {card.number}
                  </div>
                  <p className="mt-2 text-sm leading-relaxed text-muted-foreground md:text-base">
                    {card.text}
                  </p>
                  <Citation text={card.citation} className="!mt-1.5" />
                </motion.div>
              ))}
            </div>

            <div className="rounded-xl border border-primary/30 bg-primary/5 p-5 max-w-5xl mx-auto text-center">
              <p className="text-sm font-semibold uppercase tracking-[0.16em] text-primary">What prevention is worth</p>
              <p className="mt-2 text-base md:text-lg text-foreground leading-relaxed">
                A 20-truck fleet pays about <span className="font-semibold">$8,000 a year</span> for Guardian.
                One avoided fine ($7,092 average assessed) pays for the year.{' '}
                <span className="font-semibold">One avoided claim pays for fifty.</span>
              </p>
            </div>
          </div>
        </Section>

        <Section id="how-it-works" className="bg-transparent" contentClassName="max-w-7xl py-6">
          <div className="space-y-5">
            <div className="text-center space-y-3">
              <h1 className="text-3xl font-bold text-foreground md:text-5xl xl:text-6xl">
                It watches the clock, <span className="text-primary">not the driver</span>.
              </h1>
              <p className="max-w-4xl mx-auto text-lg md:text-xl text-muted-foreground">
                No cameras. No driver scoring. Legal limits in code, trade-offs in the local model,{' '}
                <span className="text-foreground">the decision with the dispatcher.</span>
              </p>
              <div className="flex flex-wrap items-center justify-center gap-2 pt-1 max-w-5xl mx-auto">
                {ingestChips.map((chip) => (
                  <span
                    key={chip}
                    className="rounded-full border border-primary/25 bg-primary/5 px-3 py-1 text-xs md:text-sm text-muted-foreground"
                  >
                    {chip}
                  </span>
                ))}
              </div>
            </div>

            <motion.div
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.6 }}
              className="flex flex-wrap items-center justify-center gap-2.5 max-w-6xl mx-auto"
            >
              {workflowSteps.map((step, index) => (
                <div key={step.title} className="flex items-center gap-2.5">
                  <LinesPatternCard className={`rounded-lg shadow-lg ${step.borderClassName}`}>
                    <LinesPatternCardBody className="flex h-12 items-center justify-center px-3 py-2 text-center">
                      <p className="text-lg font-semibold text-foreground whitespace-nowrap">{step.title}</p>
                    </LinesPatternCardBody>
                  </LinesPatternCard>

                  {index < workflowSteps.length - 1 && (
                    <div className={`text-xl font-bold ${step.arrowClassName}`}>→</div>
                  )}
                </div>
              ))}
            </motion.div>

            <motion.div
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.6, delay: 0.15 }}
            >
              <LinesPatternCard className="rounded-xl shadow-2xl border-primary/40 max-w-7xl mx-auto">
                <LinesPatternCardBody className="p-5">
                  <h3 className="text-2xl md:text-3xl font-bold text-foreground mb-5 text-center">What the dispatcher actually gets</h3>
                  <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                    {workflowArtifacts.map((artifact) => (
                      <div
                        key={artifact.title}
                        className={`rounded-xl border p-4 text-left ${artifact.bgClassName} ${artifact.borderClassName}`}
                      >
                        <p className={`text-xl md:text-2xl font-semibold ${artifact.accentClassName}`}>{artifact.title}</p>
                        <p className="mt-2 text-sm md:text-base leading-relaxed text-foreground">{artifact.text}</p>
                        <Citation text={artifact.citation} className="!mt-1.5" />
                      </div>
                    ))}
                  </div>
                </LinesPatternCardBody>
              </LinesPatternCard>
            </motion.div>

            <LinesPatternCard className="max-w-6xl mx-auto rounded-xl shadow-2xl border-primary/25">
              <LinesPatternCardBody className="p-5 text-center">
                <p className="text-lg font-semibold uppercase tracking-[0.12em] text-primary">The 90-minute delay</p>
                <p className="mt-2 text-xl md:text-2xl font-medium text-foreground leading-snug">
                  The route no longer finishes legally. Guardian prices the legal options — swap to the truck
                  already waiting, or hold and take the late fee — and the dispatcher approves one in Slack.
                </p>
              </LinesPatternCardBody>
            </LinesPatternCard>
          </div>
        </Section>

        <Section id="stack" className="bg-transparent" contentClassName="max-w-6xl py-8">
          <div className="space-y-6">
            <div className="text-center space-y-3">
              <h1 className="text-3xl font-bold text-foreground md:text-5xl xl:text-6xl">
                The whole stack runs on <span className="text-primary">one box</span>
              </h1>
              <p className="max-w-4xl mx-auto text-lg md:text-xl text-muted-foreground">
                Dell Pro Max with NVIDIA GB10. Legal limits stay in deterministic Python —{' '}
                <span className="text-foreground">the model never does legal arithmetic.</span>
              </p>
            </div>

            <div className="grid gap-5 md:grid-cols-3 max-w-6xl mx-auto text-left">
              {stackCards.map((card) => (
                <motion.div
                  key={card.title}
                  initial={{ opacity: 0, y: 24 }}
                  whileInView={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.5 }}
                  viewport={{ once: false, amount: 0.3 }}
                  className={`rounded-xl border p-6 ${card.bgClassName} ${card.borderClassName}`}
                >
                  <p className={`text-xl md:text-2xl font-semibold ${card.accentClassName}`}>{card.title}</p>
                  <p className="mt-3 text-sm md:text-base leading-relaxed text-foreground">{card.text}</p>
                </motion.div>
              ))}
            </div>

            <div className="rounded-xl border border-primary/25 bg-card/90 p-6 max-w-4xl mx-auto text-center">
              <p className="text-lg md:text-xl text-foreground leading-relaxed">
                No rates or safety records handed to a cloud vendor. No per-token bill. One box answers both.
              </p>
            </div>
          </div>
        </Section>

        <Section id="eld" className="bg-transparent" contentClassName="max-w-6xl py-8">
          <div className="space-y-6">
            <div className="text-center space-y-3">
              <h1 className="text-3xl font-bold text-foreground md:text-5xl xl:text-6xl">
                The ELD <span className="text-destructive">records</span>. It doesn&rsquo;t decide.
              </h1>
              <p className="max-w-4xl mx-auto text-lg md:text-xl text-muted-foreground">
                Every truck has had one since the mandate — and hours-of-service is still the #2 driver
                out-of-service violation at roadside.
              </p>
            </div>

            <div className="grid gap-5 md:grid-cols-3 max-w-6xl mx-auto text-left">
              {eldCards.map((card, index) => (
                <motion.div
                  key={card.title}
                  initial={{ opacity: 0, y: 24 }}
                  whileInView={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.5, delay: 0.1 * index }}
                  viewport={{ once: false, amount: 0.3 }}
                  className={`h-full rounded-xl border p-6 ${card.bgClassName} ${card.borderClassName}`}
                >
                  <p className={`text-xl md:text-2xl font-semibold ${card.accentClassName}`}>{card.title}</p>
                  <p className="mt-3 text-sm md:text-base leading-relaxed text-foreground">{card.text}</p>
                </motion.div>
              ))}
            </div>

            <div className="rounded-xl border border-primary/30 bg-primary/5 p-6 max-w-5xl mx-auto text-center">
              <p className="text-lg md:text-xl text-foreground leading-relaxed">
                Recording a violation was never the same as preventing one.{' '}
                <span className="font-semibold text-primary">Guardian keeps the balance — the reasoning runs
                on your box, the data never leaves, and the output is a decision.</span>
              </p>
              <p className="mt-3 text-sm md:text-base text-muted-foreground border-t border-border/50 pt-3">
                Nobody pairs always-on monitoring with costed recovery and local inference.
              </p>
            </div>
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
