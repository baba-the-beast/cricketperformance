import re

with open('webapp/static/pitch3d.js', 'r') as f:
    js = f.read()

# Update COLORS
js = re.sub(
    r'(const COLORS = \{)(.*?)(\};)',
    r'\1\n    dot: 0x2A354E,\n    single: 0x00E5FF,\n    boundary: 0xFFC107,\n    wicket: 0xFF2A55,\n    wide: 0xFF5E00,\n    noball: 0xCC00FF,\n  \3',
    js, flags=re.DOTALL
)

# Ground
js = re.sub(
    r'(const ground = new THREE\.Mesh\([^,]+,\n\s*new THREE\.MeshLambertMaterial\(\{ color: )0x14532d( \}\)\n\s*\);)',
    r'\g<1>0x0B0E17\2\n    const gridHelper = new THREE.GridHelper(22, 22, 0x00E5FF, 0x00E5FF);\n    gridHelper.position.y = 0.01;\n    gridHelper.material.opacity = 0.15;\n    gridHelper.material.transparent = true;\n    scene.add(gridHelper);',
    js
)

# Strip
js = re.sub(
    r'(const strip = new THREE\.Mesh\([^,]+,\n\s*new THREE\.MeshLambertMaterial\(\{ color: )0xc4b59a( \}\)\n\s*\);)',
    r'\g<1>0x151A2B\2\n    strip.material.emissive = new THREE.Color(0x00E5FF);\n    strip.material.emissiveIntensity = 0.1;',
    js
)

# Stumps
js = re.sub(
    r'(new THREE\.CylinderGeometry[^,]*,\n\s*new THREE\.MeshLambertMaterial\(\{ color: )0xf4efe4( \}\))',
    r'\g<1>0xFFFFFF\2',
    js
)

# Bowler & Batter
js = re.sub(
    r'(const bowler = new THREE\.Mesh\([^,]+,\n\s*new THREE\.MeshLambertMaterial\(\{ color: )0x7aa2ff( \}\)\n\s*\);)',
    r'\g<1>0xFF5E00\2',
    js
)
js = re.sub(
    r'(const batter = new THREE\.Mesh\([^,]+,\n\s*new THREE\.MeshLambertMaterial\(\{ color: )0xe2b857( \}\)\n\s*\);)',
    r'\g<1>0x00E5FF\2',
    js
)

# Add bloom or adjust lights
js = js.replace('scene.add(new THREE.AmbientLight(0xffffff, 0.55));', 'scene.add(new THREE.AmbientLight(0x00E5FF, 0.4));')
js = js.replace('const sun = new THREE.DirectionalLight(0xffffff, 0.7);', 'const sun = new THREE.DirectionalLight(0xFF5E00, 1.2);')

# Increase ball size and glow
js = re.sub(
    r'const ball = new THREE\.Mesh\(\n\s*new THREE\.SphereGeometry\(0\.12, 12, 12\)',
    r'const ball = new THREE.Mesh(\n      new THREE.SphereGeometry(0.18, 16, 16)',
    js
)
js = re.sub(
    r'new THREE\.MeshLambertMaterial\(\{ color: 0xef6b5b \}\)',
    r'new THREE.MeshPhysicalMaterial({ color: 0xFF5E00, emissive: 0xFF5E00, emissiveIntensity: 0.8, clearcoat: 1 })',
    js
)

# In playBall, set emissive color too
js = js.replace('ball.material.color.setHex(COLORS[kind] || COLORS.single);', 'const colorHex = COLORS[kind] || COLORS.single; ball.material.color.setHex(colorHex); ball.material.emissive.setHex(colorHex);')


with open('webapp/static/pitch3d.js', 'w') as f:
    f.write(js)
print("Updated 3D pitch")
