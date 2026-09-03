import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import gsap from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
import { Activity, ShieldCheck, Zap, ArrowRight, ShieldAlert, Volume2, VolumeX } from 'lucide-react';
import Antigravity from '../components/Antigravity';

gsap.registerPlugin(ScrollTrigger);

export function LandingPage() {
  const navigate = useNavigate();
  const [isMuted, setIsMuted] = useState(true);
  const heroRef = useRef<HTMLDivElement>(null);
  const videoContainerRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const featuresRef = useRef<HTMLDivElement>(null);
  const howItWorksRef = useRef<HTMLDivElement>(null);
  const ctaRef = useRef<HTMLDivElement>(null);

  const toggleSound = () => {
    if (videoRef.current) {
      const nextMuted = !isMuted;
      videoRef.current.muted = nextMuted;
      setIsMuted(nextMuted);
      if (!nextMuted) {
        videoRef.current.play().catch(() => {});
      }
    }
  };

  useEffect(() => {
    // Hero animations
    const heroContent = heroRef.current?.querySelectorAll('.gsap-hero');
    if (heroContent) {
      gsap.fromTo(
        heroContent,
        { y: 50, opacity: 0 },
        { y: 0, opacity: 1, duration: 1, stagger: 0.15, ease: 'power3.out', delay: 0.2 }
      );
    }

    if (videoContainerRef.current) {
      gsap.fromTo(
        videoContainerRef.current,
        { y: 40, opacity: 0, scale: 0.95 },
        { y: 0, opacity: 1, scale: 1, duration: 1.2, ease: 'power3.out', delay: 0.5 }
      );
    }

    // Features scroll animations
    const features = featuresRef.current?.querySelectorAll('.gsap-feature');
    if (features) {
      gsap.fromTo(
        features,
        { y: 50, opacity: 0 },
        {
          y: 0,
          opacity: 1,
          duration: 0.8,
          stagger: 0.15,
          ease: 'power3.out',
          scrollTrigger: {
            trigger: featuresRef.current,
            start: 'top 80%',
          },
        }
      );
    }

    // How it works scroll animations
    const steps = howItWorksRef.current?.querySelectorAll('.gsap-step');
    if (steps) {
      gsap.fromTo(
        steps,
        { x: -50, opacity: 0 },
        {
          x: 0,
          opacity: 1,
          duration: 0.8,
          stagger: 0.2,
          ease: 'power3.out',
          scrollTrigger: {
            trigger: howItWorksRef.current,
            start: 'top 75%',
          },
        }
      );
    }

    // CTA animation
    if (ctaRef.current) {
      gsap.fromTo(
        ctaRef.current,
        { scale: 0.9, opacity: 0 },
        {
          scale: 1,
          opacity: 1,
          duration: 0.8,
          ease: 'back.out(1.5)',
          scrollTrigger: {
            trigger: ctaRef.current,
            start: 'top 85%',
          },
        }
      );
    }
  }, []);

  const features = [
    {
      icon: <ShieldCheck className="w-6 h-6 text-primary" />,
      title: 'Agent Quarantine',
      description: 'Safely isolate unverified agents. Test their capabilities without risking production data or user safety.',
    },
    {
      icon: <Activity className="w-6 h-6 text-primary" />,
      title: 'Behavior Monitoring',
      description: 'Real-time telemetry and metrics for your AI agents. Track API calls, reasoning loops, and resource usage.',
    },
    {
      icon: <ShieldAlert className="w-6 h-6 text-primary" />,
      title: 'Red Teaming',
      description: 'Automated adversary simulations to uncover vulnerabilities, jailbreaks, and unintended edge cases in your agents.',
    },
    {
      icon: <Zap className="w-6 h-6 text-primary" />,
      title: 'Performance Validation',
      description: 'Benchmarking tools to ensure your agents meet SLAs, quality standards, and compliance regulations before deployment.',
    },
  ];

  const steps = [
    {
      step: '01',
      title: 'Connect Your Agent',
      description: 'Integrate your agent via our secure API or upload the model weights for localized testing.',
    },
    {
      step: '02',
      title: 'Run Simulations',
      description: 'Execute thousands of synthetic scenarios to evaluate decision-making boundaries and safety constraints.',
    },
    {
      step: '03',
      title: 'Review & Deploy',
      description: 'Analyze the comprehensive audit report and confidently deploy to production with our continuous monitoring.',
    },
  ];

  return (
    <div className="min-h-screen bg-background text-foreground overflow-hidden selection:bg-primary/20 selection:text-primary">
      {/* Navigation */}
      <nav className="fixed top-0 left-0 right-0 z-50 bg-background/80 backdrop-blur-md border-b border-border">
        <div className="container mx-auto px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-primary flex items-center justify-center text-primary-foreground font-bold text-lg">
              K
            </div>
            <span className="font-semibold text-lg tracking-tight">Know Your Agent</span>
          </div>
          <div className="flex items-center gap-4">
            <button 
              onClick={() => navigate('/store')}
              className="text-sm font-medium hover:text-primary transition-colors duration-200"
            >
              Sign In
            </button>
            <button 
              onClick={() => navigate('/store')}
              className="px-4 py-2 text-sm font-medium bg-primary text-primary-foreground rounded-md hover:bg-primary/90 transition-all duration-200 active:scale-95 shadow-sm"
            >
              Get Started
            </button>
          </div>
        </div>
      </nav>

      {/* Hero Section */}
      <section ref={heroRef} className="pt-32 pb-20 px-6 max-w-7xl mx-auto flex flex-col items-center text-center relative">
        {/* Antigravity 3D Particle Canvas behind Hero Text */}
        <div className="absolute top-12 left-1/2 -translate-x-1/2 w-full max-w-5xl h-[520px] pointer-events-auto z-0 opacity-75">
          <Antigravity
            count={300}
            magnetRadius={6}
            ringRadius={7}
            waveSpeed={0.4}
            waveAmplitude={1}
            particleSize={1.5}
            lerpSpeed={0.05}
            color={'#f38020'}
            autoAnimate={true}
            particleVariance={1}
          />
        </div>

        <div className="relative z-10 flex flex-col items-center pointer-events-none">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-primary/10 text-primary text-sm font-medium mb-8 gsap-hero border border-primary/20 backdrop-blur-xs pointer-events-auto">
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-primary opacity-75"></span>
              <span className="relative inline-flex rounded-full h-2 w-2 bg-primary"></span>
            </span>
            v2.0 is now live
          </div>
          
          <h1 className="text-5xl md:text-7xl font-bold tracking-tight mb-6 max-w-4xl text-foreground gsap-hero">
            Build Trust in Your <br className="hidden md:block" />
            <span className="text-transparent bg-clip-text bg-gradient-to-r from-primary to-orange-400">Autonomous Agents</span>
          </h1>
          
          <p className="text-lg md:text-xl text-muted-foreground max-w-2xl mb-10 gsap-hero">
            The enterprise platform for testing, validating, and managing AI agents. 
            Ensure safety, compliance, and performance before hitting production.
          </p>
          
          <div className="flex flex-col sm:flex-row items-center gap-4 gsap-hero w-full sm:w-auto pointer-events-auto">
            <button 
              onClick={() => navigate('/store')}
              className="w-full sm:w-auto px-8 py-3.5 bg-primary text-primary-foreground font-medium rounded-lg hover:bg-primary/90 transition-all duration-200 active:scale-95 shadow-lg shadow-primary/20 flex items-center justify-center gap-2 cursor-pointer"
            >
              Start Validating
              <ArrowRight className="w-4 h-4" />
            </button>
            <button 
              onClick={() => {
                const video = document.getElementById('demo-video');
                video?.scrollIntoView({ behavior: 'smooth', block: 'center' });
              }}
              className="w-full sm:w-auto px-8 py-3.5 bg-secondary text-secondary-foreground font-medium rounded-lg hover:bg-secondary/90 transition-all duration-200 active:scale-95 shadow-sm border border-border cursor-pointer"
            >
              Watch Demo
            </button>
          </div>
        </div>

        {/* Video Container */}
        <div 
          id="demo-video"
          ref={videoContainerRef} 
          className="mt-20 w-full max-w-5xl rounded-2xl overflow-hidden shadow-2xl border border-border bg-card text-left"
        >
          <div className="w-full h-11 bg-muted/60 border-b border-border flex items-center justify-between px-4">
            <div className="flex items-center gap-2">
              <div className="flex gap-1.5">
                <div className="w-3 h-3 rounded-full bg-destructive/80"></div>
                <div className="w-3 h-3 rounded-full bg-amber-400/80"></div>
                <div className="w-3 h-3 rounded-full bg-green-500/80"></div>
              </div>
              <span className="text-[11px] font-mono text-muted-foreground ml-2 hidden sm:inline-block">
                brag_product_demo.mp4
              </span>
            </div>

            {/* Sound Toggle Control in Window Header */}
            <button
              onClick={toggleSound}
              className="flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-mono font-medium transition-all duration-200 border bg-background/90 hover:bg-background text-foreground shadow-2xs hover:shadow-xs active:scale-95 cursor-pointer"
              aria-label={isMuted ? 'Unmute video' : 'Mute video'}
            >
              {isMuted ? (
                <>
                  <VolumeX className="w-3.5 h-3.5 text-muted-foreground" />
                  <span className="text-muted-foreground text-[11px]">Sound: OFF (Click to Unmute)</span>
                </>
              ) : (
                <>
                  <Volume2 className="w-3.5 h-3.5 text-primary animate-pulse" />
                  <span className="text-primary text-[11px] font-semibold">Sound: ON</span>
                </>
              )}
            </button>
          </div>

          <div className="relative group">
            <video 
              ref={videoRef}
              autoPlay 
              loop 
              muted={isMuted}
              playsInline
              className="w-full aspect-video object-cover"
            >
              <source src="/brag.mp4" type="video/mp4" />
              Your browser does not support the video tag.
            </video>

            {/* Floating Sound Toggle Pill on Video */}
            <button
              onClick={toggleSound}
              className="absolute bottom-4 right-4 z-20 flex items-center gap-2 px-3.5 py-1.5 rounded-full bg-black/75 hover:bg-black/90 text-white backdrop-blur-md text-xs font-medium border border-white/20 transition-all duration-200 active:scale-95 shadow-lg cursor-pointer"
            >
              {isMuted ? (
                <>
                  <VolumeX className="w-4 h-4 text-white/80" />
                  <span>Unmute Audio</span>
                </>
              ) : (
                <>
                  <Volume2 className="w-4 h-4 text-primary animate-pulse" />
                  <span>Mute Audio</span>
                </>
              )}
            </button>
          </div>
        </div>
      </section>

      {/* Features Section */}
      <section className="py-24 bg-muted/30 border-y border-border">
        <div className="max-w-7xl mx-auto px-6">
          <div className="text-center mb-16">
            <h2 className="text-3xl font-bold tracking-tight mb-4">Enterprise-Grade Agent Security</h2>
            <p className="text-muted-foreground max-w-2xl mx-auto">
              Everything you need to audit, monitor, and secure your autonomous workforce in one comprehensive platform.
            </p>
          </div>
          
          <div ref={featuresRef} className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
            {features.map((feature, idx) => (
              <div 
                key={idx} 
                className="gsap-feature bg-card p-6 rounded-xl border border-border/50 shadow-sm hover:shadow-md transition-all duration-300 group"
              >
                <div className="w-12 h-12 rounded-lg bg-primary/10 flex items-center justify-center mb-6 group-hover:scale-110 group-hover:bg-primary/20 transition-all duration-300">
                  {feature.icon}
                </div>
                <h3 className="text-lg font-semibold mb-2">{feature.title}</h3>
                <p className="text-sm text-muted-foreground leading-relaxed">
                  {feature.description}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* How it Works */}
      <section className="py-24 max-w-7xl mx-auto px-6">
        <div className="flex flex-col lg:flex-row items-center gap-16">
          <div className="lg:w-1/2">
            <h2 className="text-3xl md:text-4xl font-bold tracking-tight mb-6">
              Validate Before You Delegate
            </h2>
            <p className="text-lg text-muted-foreground mb-10">
              Stop guessing how your agents will behave in production. Our three-step validation framework guarantees predictable outcomes.
            </p>
            
            <div ref={howItWorksRef} className="space-y-8">
              {steps.map((step, idx) => (
                <div key={idx} className="gsap-step flex gap-4 group cursor-default">
                  <div className="flex flex-col items-center">
                    <div className="w-10 h-10 rounded-full bg-secondary text-secondary-foreground flex items-center justify-center font-bold text-sm group-hover:bg-primary group-hover:text-primary-foreground transition-colors duration-300">
                      {step.step}
                    </div>
                    {idx !== steps.length - 1 && (
                      <div className="w-0.5 h-full bg-border mt-2 group-hover:bg-primary/30 transition-colors duration-300"></div>
                    )}
                  </div>
                  <div className="pb-8">
                    <h3 className="text-xl font-semibold mb-2">{step.title}</h3>
                    <p className="text-muted-foreground">{step.description}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
          <div className="lg:w-1/2 w-full h-[500px] rounded-2xl bg-gradient-to-br from-muted to-card border border-border flex items-center justify-center p-8 relative overflow-hidden shadow-lg">
             <div className="absolute inset-0 opacity-[0.03]" style={{ backgroundImage: 'radial-gradient(circle at 2px 2px, black 1px, transparent 0)', backgroundSize: '24px 24px' }}></div>
             <div className="w-full h-full bg-background rounded-xl border border-border shadow-2xl relative z-10 flex flex-col">
                <div className="border-b border-border p-4 flex items-center justify-between bg-muted/20 rounded-t-xl">
                  <div className="font-mono text-xs font-semibold text-muted-foreground">AGENT_TEST_ENV</div>
                  <div className="flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full bg-green-500 animate-pulse"></span>
                    <span className="text-xs text-muted-foreground">Running Simulation</span>
                  </div>
                </div>
                <div className="p-4 flex-1 flex flex-col gap-3 font-mono text-sm overflow-hidden">
                   <div className="flex items-start gap-2 text-muted-foreground"><span className="text-primary">{'>'}</span> Initializing agent context... [OK]</div>
                   <div className="flex items-start gap-2 text-muted-foreground"><span className="text-primary">{'>'}</span> Loading adversary dataset (10,000 cases)... [OK]</div>
                   <div className="flex items-start gap-2 text-foreground"><span className="text-primary">{'>'}</span> Executing Test Suite #A-94...</div>
                   
                   <div className="mt-4 border-l-2 border-primary/50 pl-3 ml-2 space-y-2 opacity-80">
                     <div className="text-xs flex justify-between"><span>Prompt injection attempt</span> <span className="text-green-500">BLOCKED</span></div>
                     <div className="text-xs flex justify-between"><span>Data exfiltration attempt</span> <span className="text-green-500">BLOCKED</span></div>
                     <div className="text-xs flex justify-between"><span>Unauthorized API access</span> <span className="text-green-500">BLOCKED</span></div>
                   </div>

                   <div className="mt-4 flex items-start gap-2 text-green-600 font-semibold"><span className="text-green-500">{'>'}</span> Validation Complete. Agent is safe for deployment.</div>
                </div>
             </div>
          </div>
        </div>
      </section>

      {/* CTA Section */}
      <section className="py-24 px-6">
        <div 
          ref={ctaRef}
          className="max-w-5xl mx-auto bg-primary rounded-3xl p-10 md:p-16 text-center text-primary-foreground relative overflow-hidden shadow-xl"
        >
          {/* Background decoration */}
          <div className="absolute top-0 right-0 -mt-16 -mr-16 w-64 h-64 rounded-full bg-white opacity-10 blur-3xl"></div>
          <div className="absolute bottom-0 left-0 -mb-16 -ml-16 w-64 h-64 rounded-full bg-black opacity-10 blur-3xl"></div>
          
          <div className="relative z-10">
            <h2 className="text-3xl md:text-5xl font-bold tracking-tight mb-6 text-white">
              Ready to deploy agents with confidence?
            </h2>
            <p className="text-primary-foreground/80 text-lg mb-10 max-w-2xl mx-auto">
              Join leading enterprises that trust Know Your Agent to secure and validate their autonomous systems.
            </p>
            <button 
              onClick={() => navigate('/store')}
              className="px-8 py-4 bg-background text-foreground font-semibold rounded-lg hover:bg-background/90 transition-all duration-200 active:scale-95 shadow-lg flex items-center justify-center gap-2 mx-auto"
            >
              Get Started for Free
              <ArrowRight className="w-5 h-5" />
            </button>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t border-border bg-background py-12 px-6">
        <div className="max-w-7xl mx-auto flex flex-col md:flex-row justify-between items-center gap-6">
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 rounded bg-primary flex items-center justify-center text-primary-foreground font-bold text-xs">
              K
            </div>
            <span className="font-semibold text-sm">Know Your Agent &copy; {new Date().getFullYear()}</span>
          </div>
          <div className="flex gap-6 text-sm text-muted-foreground">
            <a href="#" className="hover:text-foreground transition-colors">Documentation</a>
            <a href="#" className="hover:text-foreground transition-colors">Privacy</a>
            <a href="#" className="hover:text-foreground transition-colors">Terms</a>
          </div>
        </div>
      </footer>
    </div>
  );
}
