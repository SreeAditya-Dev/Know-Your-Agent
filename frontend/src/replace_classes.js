import fs from 'fs';
import path from 'path';

const classMap = {
  'bg-paper': 'bg-card',
  'bg-canvas': 'bg-background',
  'text-ink': 'text-foreground',
  'text-ink-soft': 'text-muted-foreground',
  'text-ink-faint': 'text-muted-foreground/70',
  'border-line-soft': 'border-border/50',
  'border-line': 'border-border',
  'bg-chrome-line': 'bg-secondary/80',
  'bg-chrome': 'bg-secondary',
  'border-chrome-line': 'border-border',
  'text-chrome-text': 'text-muted-foreground',
  'text-chrome-dim': 'text-muted-foreground/60',
  'text-paper': 'text-card-foreground',
  'bg-signal-bg': 'bg-primary/10',
  'bg-signal/90': 'bg-primary/90',
  'bg-signal/40': 'bg-primary/40',
  'bg-signal': 'bg-primary',
  'border-signal/40': 'border-primary/40',
  'border-signal': 'border-primary',
  'text-signal': 'text-primary',
  'bg-allow-bg': 'bg-green-500/10',
  'bg-allow': 'bg-green-600',
  'text-allow': 'text-green-600',
  'border-allow': 'border-green-600',
  'bg-quarantine-bg': 'bg-orange-500/10',
  'bg-quarantine': 'bg-orange-500',
  'text-quarantine': 'text-orange-500',
  'border-quarantine': 'border-orange-500',
  'bg-deny-bg': 'bg-destructive/10',
  'bg-deny': 'bg-destructive',
  'text-deny': 'text-destructive',
  'border-deny': 'border-destructive',
  'bg-step-up-bg': 'bg-blue-500/10',
  'bg-step-up': 'bg-blue-600',
  'text-step-up': 'text-blue-600',
  'border-step-up': 'border-blue-600',
  'bg-\\[\\#fafbfc\\]': 'bg-muted/30',
  'bg-\\[\\#f1f3f6\\]': 'bg-muted/50',
  'bg-\\[\\#f8f9fb\\]': 'bg-muted/20',
  'text-\\[\\#2c333d\\]': 'text-foreground/80',
};

function walk(dir) {
  let results = [];
  const list = fs.readdirSync(dir);
  list.forEach(function(file) {
    file = path.join(dir, file);
    const stat = fs.statSync(file);
    if (stat && stat.isDirectory()) { 
      results = results.concat(walk(file));
    } else { 
      if (file.endsWith('.tsx') || file.endsWith('.ts')) {
        results.push(file);
      }
    }
  });
  return results;
}

const files = walk('./');
let changedFiles = 0;

files.forEach(file => {
  let content = fs.readFileSync(file, 'utf8');
  let originalContent = content;
  
  // Sort keys by length descending to replace longer classes first (e.g., bg-signal-bg before bg-signal)
  const sortedKeys = Object.keys(classMap).sort((a, b) => b.length - a.length);
  
  sortedKeys.forEach(oldClass => {
    const newClass = classMap[oldClass];
    // Create a regex to match the class as a whole word, allowing for brackets if it's a tailwind arbitrary value
    const regexStr = oldClass.includes('[') 
        ? oldClass 
        : `\\b${oldClass}\\b`;
    
    // Quick and dirty string replace for specific cases, or regex for word boundaries
    if (oldClass.includes('[')) {
        content = content.replaceAll(oldClass.replace(/\\/g, ''), newClass);
    } else {
        const regex = new RegExp(regexStr, 'g');
        content = content.replace(regex, newClass);
    }
  });

  // Manual fixes for "text-card-foreground" where it should be white inside primary/secondary
  content = content.replace(/bg-secondary text-card-foreground/g, 'bg-secondary text-secondary-foreground');
  content = content.replace(/bg-primary text-card-foreground/g, 'bg-primary text-primary-foreground');
  content = content.replace(/bg-secondary hover:bg-secondary\/80 rounded font-medium text-xs cursor-pointer/g, 'bg-secondary text-secondary-foreground hover:bg-secondary/80 rounded font-medium text-xs cursor-pointer');

  if (content !== originalContent) {
    fs.writeFileSync(file, content, 'utf8');
    console.log(`Updated ${file}`);
    changedFiles++;
  }
});

console.log(`Updated ${changedFiles} files.`);
