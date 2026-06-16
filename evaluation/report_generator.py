import pandas as pd
import matplotlib.pyplot as plt
import os

def generate_reports(csv_file):
    if not os.path.exists(csv_file):
        print(f"No results found at {csv_file}")
        return
        
    df = pd.DataFrame(pd.read_csv(csv_file))
    
    # Bitrate vs VMAF
    plt.figure(figsize=(8, 5))
    plt.plot(df['bitrate_kbps'], df['vmaf'], marker='o', linestyle='-', color='b')
    for i, txt in enumerate(df['config']):
        plt.annotate(txt, (df['bitrate_kbps'][i], df['vmaf'][i]))
    plt.title('Bitrate vs VMAF Quality')
    plt.xlabel('Bitrate (kbps)')
    plt.ylabel('VMAF Score')
    plt.grid(True)
    plt.savefig('bitrate_vs_vmaf.png')
    print("Saved bitrate_vs_vmaf.png")

if __name__ == "__main__":
    generate_reports("benchmark_results.csv")
