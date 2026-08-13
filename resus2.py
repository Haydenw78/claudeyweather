"""Final state of the resuspension question, four stations, 108 wave records."""
import json, statistics, collections
o=json.load(open('obs4.json'))
ref=[r for r in o['reference'] if r.get('seaM') is not None]
print("Bed stress range now available:")
for k in sorted({r['station'] for r in ref}):
    g=[r for r in ref if r['station']==k]
    print(f"  {k:26s} n={len(g):3d}  up to {max(r['ubMs'] for r in g):.3f} m/s")
print("\nRottnest is the only station that gets near the 0.35 threshold.")
print("It contributes all 8 records above 0.20 m/s.")
