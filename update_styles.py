import re

with open('webapp/static/styles.css', 'r') as f:
    css = f.read()

# Make main and app-shell have perspective
css = re.sub(r'(\.app-shell\s*\{\s*)(.*?\} )', r'\1perspective: 1200px; \2', css)

# Update card hover to include 3D transforms
css = re.sub(
    r'(\.card:hover\s*\{\s*transform:\s*)(.*?)(;)',
    r'\1translateY(-4px) rotateX(2deg) rotateY(-2deg)\3',
    css
)
css = re.sub(
    r'(\.card\s*\{[^\}]*)',
    r'\1 transform-style: preserve-3d;',
    css
)

# Update replay-ball
css = re.sub(
    r'(\.replay-ball\s*\{)(.*?)(})',
    r'''\1 \2
  animation: pop3D 0.6s cubic-bezier(0.175, 0.885, 0.32, 1.275) both;
  transform-origin: top;
  transform-style: preserve-3d;
  background: rgba(13, 17, 28, 0.7);
  border: 1px solid rgba(255,255,255,0.03);
  border-left: 4px solid var(--stroke);
  border-radius: 8px;
  margin-bottom: 12px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.4), inset 0 1px 3px rgba(255,255,255,0.05);
  border-bottom: none;
}
@keyframes pop3D {
  0% { opacity: 0; transform: translateZ(-80px) translateY(-10px) rotateX(45deg); }
  100% { opacity: 1; transform: translateZ(0) translateY(0) rotateX(0); }
}
.replay-ball:hover {
  transform: translateZ(10px) scale(1.02);
  background: rgba(22, 28, 44, 0.9);
  transition: transform 0.2s ease, background 0.2s ease;
}
.replay-ball.boundary { border-left-color: var(--gold); }
.replay-ball.wicket { border-left-color: var(--red); }
''', css, flags=re.DOTALL)

# Replay Live
css = re.sub(
    r'(\.replay-live\s*\{)(.*?)(})',
    r'''\1 \2
  transform-style: preserve-3d;
  box-shadow: 0 15px 40px rgba(0, 229, 255, 0.15), inset 0 2px 10px rgba(255,255,255,0.1);
}
.replay-live::after {
  content: ""; position: absolute; top: 0; left: 0; right: 0; bottom: 0;
  background: radial-gradient(circle at 80% 20%, rgba(0,229,255,0.1), transparent 50%);
  transform: translateZ(-20px); pointer-events: none;
''', css, flags=re.DOTALL)

# Moment list (Simulation)
css = re.sub(
    r'(\.moment-list li\s*\{)(.*?)(})',
    r'''\1 \2
  animation: pop3D 0.5s cubic-bezier(0.175, 0.885, 0.32, 1.275) both;
  transform-style: preserve-3d;
  transition: transform 0.2s ease;
}
.moment-list li:hover {
  transform: translateZ(15px) translateX(5px);
  background: rgba(22, 28, 44, 0.9);
  box-shadow: -5px 10px 20px rgba(0,0,0,0.5);
''', css, flags=re.DOTALL)

# Score pills
css = re.sub(
    r'(\.score-pill\s*\{)(.*?)(})',
    r'''\1 \2
  transform-style: preserve-3d;
  transition: transform 0.4s cubic-bezier(0.16, 1, 0.3, 1), box-shadow 0.4s ease;
}
.score-pill:hover {
  transform: translateY(-5px) translateZ(20px) rotateX(5deg) rotateY(-5deg);
  box-shadow: -8px 15px 30px rgba(0,0,0,0.6), inset 0 2px 10px rgba(255,255,255,0.08);
''', css, flags=re.DOTALL)

# Button 3D click/hover
css = re.sub(
    r'(\.btn:hover\s*\{.*?)(})',
    r'\1 box-shadow: 0 5px 25px var(--accent-glow); transform: translateY(-2px) translateZ(10px); }',
    css, flags=re.DOTALL
)
css += '''
.btn:active {
  transform: translateY(2px) translateZ(0px);
  box-shadow: 0 0 10px var(--accent-glow);
}
'''

# Pitch player label 3D
css = re.sub(
    r'(\.pitch-player-label\s*\{[^\}]*\})',
    r'\1\n.pitch-player-label { transform-style: preserve-3d; }',
    css
)

with open('webapp/static/styles.css', 'w') as f:
    f.write(css)
print("Updated CSS with 3D effects")
