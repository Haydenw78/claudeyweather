import math, statistics, collections

def rank(a):
    idx = sorted(range(len(a)), key=lambda i: a[i]); r = [0.0]*len(a); i = 0
    while i < len(idx):
        j = i
        while j+1 < len(idx) and a[idx[j+1]] == a[idx[i]]: j += 1
        for k in range(i, j+1): r[idx[k]] = (i+j)/2 + 1
        i = j+1
    return r

def pearson(x, y):
    n = len(x)
    if n < 3: return 0.0
    mx, my = sum(x)/n, sum(y)/n
    num = sum((x[i]-mx)*(y[i]-my) for i in range(n))
    dx = math.sqrt(sum((v-mx)**2 for v in x)); dy = math.sqrt(sum((v-my)**2 for v in y))
    return num/(dx*dy) if dx and dy else 0.0

def spearman(x, y): return pearson(rank(x), rank(y))

def crit(n): return 1.96/math.sqrt(max(1, n-2+1.96**2))

def deseason(vals, months):
    b = collections.defaultdict(list)
    for v, m in zip(vals, months): b[m].append(v)
    md = {m: statistics.median(v) for m, v in b.items()}
    return [v - md[m] for v, m in zip(vals, months)]
