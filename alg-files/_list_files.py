
import os
import glob

base = "/home/sean/work/orientom/QUWA/alg-files"
files = sorted(glob.glob(f"{base}/*.py"))
for f in files:
    print(f)
