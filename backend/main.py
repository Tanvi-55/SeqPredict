# backend/main.py

import os
import time
import re
import shutil
import random
import warnings
from typing import List, Optional

from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware  # Import CORS middleware
from dotenv import load_dotenv
import requests
from urllib.parse import urlencode
from Bio.PDB import PDBParser
from Bio.Blast import NCBIWWW, NCBIXML
from Bio.SeqUtils.ProtParam import ProteinAnalysis

# Load environment variables from .env file
load_dotenv()

app = FastAPI()

# =======================
# CORS Configuration
# =======================

# Define the allowed origins. Replace <YOUR_FRONTEND_IP> and <PORT> with your frontend's IP and port.
origins = [
    "http://localhost:8000",            # FastAPI backend (if accessed directly)
    "http://10.0.2.2:8000",             # Android emulator accessing localhost
    "http://localhost:3000",            # Example: React frontend
    "http://192.168.1.100:8080",        # Flutter frontend
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,               # Allows specific origins
    allow_credentials=True,
    allow_methods=["*"],                 # Allows all HTTP methods
    allow_headers=["*"],                 # Allows all headers
)

# =======================
# Constants
# =======================

PORT = int(os.getenv('PORT', 8000))
BLAST_URL = os.getenv('BLAST_URL', 'https://blast.ncbi.nlm.nih.gov/blast/Blast.cgi')
POLL_INTERVAL_MS = int(os.getenv('POLL_INTERVAL_MS', 5000))  # in milliseconds
UPLOAD_DIR = 'uploads/'

# Ensure the upload directory exists
os.makedirs(UPLOAD_DIR, exist_ok=True)

# =======================
# Utility Functions
# =======================

def adjust_program_param(program: str) -> str:
    """
    Adjust the program parameter based on specific cases.
    """
    if program.lower() == 'megablast':
        return 'blastn&MEGABLAST=on'
    elif program.lower() == 'rpsblast':
        return 'blastp&SERVICE=rpsblast'
    return program

def encode_queries(files: List[UploadFile]) -> str:
    """
    Read and URL-encode the contents of the uploaded query files.
    """
    encoded_query = ''
    for file in files:
        file_location = os.path.join(UPLOAD_DIR, file.filename)
        with open(file_location, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        with open(file_location, "r", encoding='utf-8') as f:
            data = f.read()
            encoded_query += requests.utils.quote(data)
        os.remove(file_location)  # Delete the file after reading
    return encoded_query

def generate_random_sequence(length: int) -> str:
    """
    Generate a random protein sequence of given length.
    """
    amino_acids = "ACDEFGHIKLMNPQRSTVWY"  # Standard amino acids
    return ''.join(random.choice(amino_acids) for _ in range(length))

def find_orfs(dna_sequence: str) -> List[str]:
    """
    Find all ORFs in a DNA sequence.
    """
    start_codon = "ATG"
    stop_codons = {"TAA", "TAG", "TGA"}
    orfs = []
    dna_length = len(dna_sequence)

    # Loop through all possible start positions
    for i in range(dna_length - 2):
        codon = dna_sequence[i:i+3]
        # Check for start codon
        if codon == start_codon:
            # Search for stop codon from the position of the start codon
            for j in range(i + 3, dna_length - 2, 3):
                codon = dna_sequence[j:j+3]
                # If stop codon is found, save the ORF and break out of the inner loop
                if codon in stop_codons:
                    orfs.append(dna_sequence[i:j+3])
                    break
    return orfs

def analyze_protein_sequence(protein_sequence: str) -> dict:
    """
    Analyze a protein sequence using ProtParam.
    """
    analyzed_seq = ProteinAnalysis(protein_sequence)
    molecular_weight = analyzed_seq.molecular_weight()
    instability_index = analyzed_seq.instability_index()
    gravy = analyzed_seq.gravy()
    return {
        "molecular_weight": molecular_weight,
        "instability_index": instability_index,
        "gravy": gravy
    }

# =======================
# BLAST Endpoints
# =======================

@app.post("/blast/submit")
async def submit_blast_job(
    program: str = Form(...),
    database: str = Form(...),
    queries: List[UploadFile] = File(...)
):
    """
    Submit a BLAST job.
    """
    if not program or not database or not queries:
        raise HTTPException(
            status_code=400,
            detail="Missing required fields: program, database, and at least one query file."
        )

    program_param = adjust_program_param(program)

    try:
        encoded_query = encode_queries(queries)
    except Exception as e:
        print(f"Error reading query files: {e}")
        raise HTTPException(status_code=500, detail="Failed to read query files.")

    # Build the request parameters
    params = {
        'CMD': 'Put',
        'PROGRAM': program_param,
        'DATABASE': database,
        'QUERY': encoded_query
    }

    try:
        response = requests.post(
            BLAST_URL,
            data=urlencode(params),
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        )
        response.raise_for_status()
    except Exception as e:
        print(f"Error submitting BLAST request: {e}")
        raise HTTPException(status_code=500, detail="Failed to submit BLAST request.")

    # Extract RID and RTOE using regex
    rid_match = re.search(r"RID\s+=\s+(\S+)", response.text)
    rtoe_match = re.search(r"RTOE\s+=\s+(\d+)", response.text)

    if rid_match and rtoe_match:
        rid = rid_match.group(1)
        rtoe = int(rtoe_match.group(1))
        return {"rid": rid, "message": "Job submitted successfully."}
    else:
        print("Failed to retrieve RID and RTOE from the response.")
        raise HTTPException(status_code=500, detail="Failed to retrieve job ID.")

@app.get("/blast/status/{rid}")
async def check_blast_status(rid: str):
    """
    Check the status of a BLAST job.
    """
    if not rid:
        raise HTTPException(status_code=400, detail="RID is required.")

    params = {
        'CMD': 'Get',
        'FORMAT_OBJECT': 'SearchInfo',
        'RID': rid
    }

    try:
        response = requests.get(BLAST_URL, params=params)
        response.raise_for_status()
        content = response.text

        if re.search(r"Status=WAITING", content):
            return {"status": "WAITING", "message": "BLAST search is in progress."}
        elif re.search(r"Status=FAILED", content):
            return {"status": "FAILED", "message": "BLAST search failed. Please report to blast-help@ncbi.nlm.nih.gov."}
        elif re.search(r"Status=UNKNOWN", content):
            return {"status": "UNKNOWN", "message": "BLAST search expired or RID is invalid."}
        elif re.search(r"Status=READY", content):
            if re.search(r"ThereAreHits=yes", content):
                return {"status": "READY", "message": "BLAST search completed with hits."}
            else:
                return {"status": "READY", "message": "BLAST search completed with no hits."}
        else:
            print("Unexpected status in BLAST response.")
            raise HTTPException(status_code=500, detail="Unexpected response from BLAST server.")
    except Exception as e:
        print(f"Error checking BLAST status: {e}")
        raise HTTPException(status_code=500, detail="Failed to check BLAST job status.")

@app.get("/blast/result/{rid}")
async def get_blast_result(rid: str):
    """
    Retrieve the results of a BLAST job.
    """
    if not rid:
        raise HTTPException(status_code=400, detail="RID is required.")

    params = {
        'CMD': 'Get',
        'FORMAT_TYPE': 'Text',
        'RID': rid
    }

    try:
        response = requests.get(BLAST_URL, params=params)
        response.raise_for_status()
        content = response.text

        if re.match(r"^\s+RID\s+=\s+", content, re.MULTILINE):
            return {"result": content}
        elif re.search(r"Status=WAITING", content):
            raise HTTPException(
                status_code=400,
                detail="Results are not ready yet. Please check the job status."
            )
        elif re.search(r"Status=FAILED", content):
            raise HTTPException(
                status_code=400,
                detail="BLAST search failed. Please report to blast-help@ncbi.nlm.nih.gov."
            )
        elif re.search(r"Status=UNKNOWN", content):
            raise HTTPException(
                status_code=400,
                detail="BLAST search expired or RID is invalid."
            )
        else:
            print("Unexpected content in BLAST result response.")
            raise HTTPException(status_code=500, detail="Unexpected response from BLAST server.")
    except HTTPException as he:
        raise he
    except Exception as e:
        print(f"Error retrieving BLAST results: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve BLAST results.")

@app.post("/blast/poll-and-retrieve")
async def poll_and_retrieve_blast(
    program: str = Form(...),
    database: str = Form(...),
    queries: List[UploadFile] = File(...)
):
    """
    Submit a BLAST job, wait for completion, and retrieve results.
    """
    if not program or not database or not queries:
        raise HTTPException(
            status_code=400,
            detail="Missing required fields: program, database, and at least one query file."
        )

    program_param = adjust_program_param(program)

    try:
        encoded_query = encode_queries(queries)
    except Exception as e:
        print(f"Error reading query files: {e}")
        raise HTTPException(status_code=500, detail="Failed to read query files.")

    # Build the request parameters for submission
    params = {
        'CMD': 'Put',
        'PROGRAM': program_param,
        'DATABASE': database,
        'QUERY': encoded_query
    }

    try:
        submit_response = requests.post(
            BLAST_URL,
            data=urlencode(params),
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        )
        submit_response.raise_for_status()
    except Exception as e:
        print(f"Error submitting BLAST request: {e}")
        raise HTTPException(status_code=500, detail="Failed to submit BLAST request.")

    # Extract RID and RTOE
    rid_match = re.search(r"RID\s+=\s+(\S+)", submit_response.text)
    rtoe_match = re.search(r"RTOE\s+=\s+(\d+)", submit_response.text)

    if rid_match and rtoe_match:
        rid = rid_match.group(1)
        rtoe = int(rtoe_match.group(1))
    else:
        print("Failed to retrieve RID and RTOE from the response.")
        raise HTTPException(status_code=500, detail="Failed to retrieve job ID.")

    # Wait for the estimated time to completion
    time.sleep(rtoe)

    # Poll for results
    while True:
        time.sleep(POLL_INTERVAL_MS / 1000)  # Convert ms to seconds

        status_params = {
            'CMD': 'Get',
            'FORMAT_OBJECT': 'SearchInfo',
            'RID': rid
        }

        try:
            status_response = requests.get(BLAST_URL, params=status_params)
            status_response.raise_for_status()
            content = status_response.text

            if re.search(r"Status=WAITING", content):
                continue  # Still searching

            if re.search(r"Status=FAILED", content):
                raise HTTPException(
                    status_code=500,
                    detail=f"Search {rid} failed; please report to blast-help@ncbi.nlm.nih.gov."
                )

            if re.search(r"Status=UNKNOWN", content):
                raise HTTPException(
                    status_code=400,
                    detail="Search expired or RID is invalid."
                )

            if re.search(r"Status=READY", content):
                if re.search(r"ThereAreHits=yes", content):
                    # Retrieve results
                    result_params = {
                        'CMD': 'Get',
                        'FORMAT_TYPE': 'Text',
                        'RID': rid
                    }

                    try:
                        result_response = requests.get(BLAST_URL, params=result_params)
                        result_response.raise_for_status()
                        result_content = result_response.text
                        return {"result": result_content}
                    except Exception as e:
                        print(f"Error retrieving BLAST results: {e}")
                        raise HTTPException(status_code=500, detail="Failed to retrieve BLAST results.")
                else:
                    return {"message": "No hits found."}

            # Unexpected status
            print("Unexpected status while polling for results.")
            raise HTTPException(status_code=500, detail="An unexpected error occurred while polling for results.")

        except HTTPException as he:
            raise he
        except Exception as e:
            print(f"Error polling for BLAST results: {e}")
            raise HTTPException(status_code=500, detail="Failed to poll for BLAST results.")

# =======================
# Protein Generation and Analysis Endpoints
# =======================

@app.post("/protein/generate")
async def generate_protein_sequence(length: int = Form(...)):
    """
    Generate a random protein sequence and save it to a FASTA file.
    """
    try:
        protein_sequence = generate_random_sequence(length)
        fasta_content = f">Random_Protein\n{protein_sequence}\n"
        fasta_filename = "protein_sequence.fasta"
        with open(fasta_filename, "w") as f:
            f.write(fasta_content)
        return {
            "message": "Protein sequence generated and saved to protein_sequence.fasta.",
            "protein_sequence": protein_sequence
        }
    except Exception as e:
        print(f"Error generating protein sequence: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate protein sequence.")

@app.post("/protein/mock_structure")
async def mock_structure_prediction():
    """
    Create a mock PDB file and analyze its structure.
    """
    mock_pdb_content = """
    ATOM      1  N   MET A   1      20.154  34.234  27.568  1.00 20.00           N  
    ATOM      2  CA  MET A   1      21.217  35.123  27.251  1.00 20.00           C  
    ATOM      3  C   MET A   1      22.643  34.789  27.899  1.00 20.00           C  
    ATOM      4  O   MET A   1      23.428  35.688  28.474  1.00 20.00           O  
    ATOM      5  CB  MET A   1      21.012  36.421  26.633  1.00 20.00           C  
    """

    pdb_filename = "mock_protein_model.pdb"
    try:
        with open(pdb_filename, "w") as f:
            f.write(mock_pdb_content)
        print("Mock PDB model saved to mock_protein_model.pdb.")

        # Analyze the structure
        parser = PDBParser()
        structure = parser.get_structure('Mock_Protein', pdb_filename)
        analysis = []
        for model in structure:
            for chain in model:
                chain_info = {
                    "chain_id": chain.id,
                    "num_residues": len(chain),
                    "residues": []
                }
                for residue in chain:
                    residue_info = {
                        "resname": residue.resname,
                        "id": residue.id
                    }
                    chain_info["residues"].append(residue_info)
                analysis.append(chain_info)
        return {
            "message": "Mock PDB model created and analyzed.",
            "structure_analysis": analysis
        }
    except Exception as e:
        print(f"Error in mock structure prediction: {e}")
        raise HTTPException(status_code=500, detail="Failed to create and analyze mock PDB model.")

@app.post("/blast/run_nr")
async def run_nr_blast(
    protein_sequence: str = Form(...)
):
    """
    Run BLASTP against the NR database.
    """
    try:
        print("Running NR BLAST search...")
        result_handle = NCBIWWW.qblast("blastp", "nr", protein_sequence)
        print("NR BLAST search completed.")

        # Save the results to an XML file
        xml_filename = "blast_results_nr.xml"
        with open(xml_filename, "w") as out_file:
            out_file.write(result_handle.read())
        result_handle.close()
        print(f"NR BLAST results saved to {xml_filename}.")

        # Parse the BLAST results
        with open(xml_filename) as result_handle:
            blast_record = NCBIXML.read(result_handle)

        # Extract top 5 hits
        top_hits = []
        for alignment in blast_record.alignments[:5]:
            hit_info = {
                "title": alignment.title,
                "length": alignment.length,
                "hsps": []
            }
            for hsp in alignment.hsps:
                hsp_info = {
                    "e_value": hsp.expect,
                    "identities": hsp.identities,
                    "alignment": {
                        "query": hsp.query,
                        "match": hsp.match,
                        "subject": hsp.sbjct
                    }
                }
                hit_info["hsps"].append(hsp_info)
            top_hits.append(hit_info)

        return {
            "message": "NR BLAST search completed.",
            "top_hits": top_hits
        }
    except Exception as e:
        print(f"An error occurred during NR BLAST search: {e}")
        raise HTTPException(status_code=500, detail="Failed to perform NR BLAST search.")

@app.post("/blast/run_pdb")
async def run_pdb_blast(
    protein_sequence: str = Form(...)
):
    """
    Run BLASTP against the PDB database.
    """
    try:
        warnings.simplefilter('ignore')  # Ignore Biopython warnings
        print("Running PDB BLAST search...")
        result_handle = NCBIWWW.qblast("blastp", "pdb", protein_sequence)
        print("PDB BLAST search completed.")

        # Save the results to an XML file
        xml_filename = "blast_results_pdb.xml"
        with open(xml_filename, "w") as out_file:
            out_file.write(result_handle.read())
        result_handle.close()
        print(f"PDB BLAST results saved to {xml_filename}.")

        # Parse the BLAST results
        with open(xml_filename) as result_handle:
            blast_records = NCBIXML.parse(result_handle)
            blast_records = list(blast_records)  # Convert generator to list

        # Extract relevant information
        results = []
        for blast_record in blast_records:
            record_info = {
                "query": blast_record.query,
                "alignments": []
            }
            for alignment in blast_record.alignments:
                for hsp in alignment.hsps:
                    alignment_info = {
                        "match": alignment.title,
                        "score": hsp.score,
                        "e_value": hsp.expect,
                        "query_start": hsp.query_start,
                        "query_end": hsp.query_end,
                        "hit_start": hsp.sbjct_start if hasattr(hsp, 'sbjct_start') else None,
                        "hit_end": hsp.sbjct_end if hasattr(hsp, 'sbjct_end') else None,
                        "alignment": hsp.sbjct,
                        "identities": hsp.identities,
                        "percent_identity": (hsp.identities / hsp.align_length) * 100,
                        "gaps": hsp.gaps,
                        "alignment_length": hsp.align_length
                    }
                    record_info["alignments"].append(alignment_info)
            results.append(record_info)

        return {
            "message": "PDB BLAST search completed.",
            "results": results
        }
    except Exception as e:
        print(f"An error occurred during PDB BLAST search: {e}")
        raise HTTPException(status_code=500, detail="Failed to perform PDB BLAST search.")

@app.post("/blast/run_swissprot")
async def run_swissprot_blast(
    protein_sequence: str = Form(...)
):
    """
    Run BLASTP against the SwissProt database.
    """
    try:
        warnings.simplefilter('ignore', category=BiopythonWarning)
        print("Starting SwissProt BLAST search...")
        result_handle = NCBIWWW.qblast("blastp", "swissprot", protein_sequence, expect=1e-3)
        print("SwissProt BLAST search completed.")

        # Save the results to an XML file
        xml_filename = "blast_result_swissprot.xml"
        with open(xml_filename, "w") as out_handle:
            out_handle.write(result_handle.read())
        result_handle.close()
        print(f"SwissProt BLAST results saved to {xml_filename}.")

        # Parse the BLAST results
        with open(xml_filename) as result_handle:
            blast_records = NCBIXML.parse(result_handle)
            blast_records = list(blast_records)  # Convert generator to list

        # Extract relevant information
        results = []
        for blast_record in blast_records:
            record_info = {
                "query": blast_record.query,
                "alignments": []
            }
            for alignment in blast_record.alignments:
                for hsp in alignment.hsps:
                    alignment_info = {
                        "match": alignment.title,
                        "score": hsp.score,
                        "e_value": getattr(hsp, 'evalue', hsp.expect),
                        "query_start": hsp.query_start,
                        "query_end": hsp.query_end,
                        "hit_start": getattr(hsp, 'hit_start', None),
                        "hit_end": getattr(hsp, 'hit_end', None),
                        "alignment": hsp.sbjct,
                        "identities": hsp.identities,
                        "gaps": hsp.gaps,
                        "alignment_length": hsp.align_length,
                        "percent_identity": (hsp.identities / hsp.align_length) * 100
                    }
                    record_info["alignments"].append(alignment_info)
            results.append(record_info)

        return {
            "message": "SwissProt BLAST search completed.",
            "results": results
        }
    except Exception as e:
        print(f"An error occurred during SwissProt BLAST search: {e}")
        raise HTTPException(status_code=500, detail="Failed to perform SwissProt BLAST search.")

@app.post("/protein/analyze")
async def analyze_protein(
    protein_sequence: str = Form(...)
):
    """
    Analyze a protein sequence using ProtParam.
    """
    try:
        analysis = analyze_protein_sequence(protein_sequence)
        return {
            "molecular_weight": analysis["molecular_weight"],
            "instability_index": analysis["instability_index"],
            "gravy": analysis["gravy"]
        }
    except Exception as e:
        print(f"Error analyzing protein sequence: {e}")
        raise HTTPException(status_code=500, detail="Failed to analyze protein sequence.")

# =======================
# ORF Finder Endpoint
# =======================

@app.post("/orf/find")
async def find_orfs_endpoint(
    dna_sequence: str = Form(...)
):
    """
    Find ORFs in a given DNA sequence.
    """
    try:
        orfs = find_orfs(dna_sequence)
        return {
            "found_orfs": orfs
        }
    except Exception as e:
        print(f"Error finding ORFs: {e}")
        raise HTTPException(status_code=500, detail="Failed to find ORFs in the DNA sequence.")

# =======================
# Health Check Endpoint
# =======================

@app.get("/")
async def health_check():
    """
    Health Check Endpoint
    """
    return {"message": "BLAST API Server is running."}

# =======================
# Run Instructions
# =======================

# To run the server, use the command:
# uvicorn main:app --host 0.0.0.0 --port 8000
