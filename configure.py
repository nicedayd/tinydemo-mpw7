#!/usr/bin/env python3
import shutil, os, sys
from urllib.parse import urlparse
import argparse
import requests
import base64
import zipfile
import io
import logging
# list of designs
from designs import designs


# where are things
proj_name = "scan_wrapper_lesson_1"

class Project():
    
    def __init__(giturl, name):


# download the artifact for each project to get the gds & lef
def get_macros():
    from tokens import git_token, git_username

    # iterate through all designs
    macro_number = 0
    git_url = designs[macro_number]

    res = urlparse(git_url)
    try:
        _, user_name, repo = res.path.split('/')
    except ValueError:
        logging.error("couldn't split repo from {}".format(git_url))
        exit(1)
    repo = repo.replace('.git', '')

    # authenticate for rate limiting
    auth_string = git_username + ':' + git_token
    encoded = base64.b64encode(auth_string.encode('ascii'))
    headers = {
        "authorization" : 'Basic ' + encoded.decode('ascii'),
        "Accept"        : "application/vnd.github+json",
        }

    api_url = 'https://api.github.com/repos/{}/{}/actions/artifacts'.format(user_name, repo)
    r = requests.get(api_url, headers=headers)
    requests_remaining = int(r.headers['X-RateLimit-Remaining'])
    if requests_remaining == 0:
        logging.error("no API requests remaining")
        exit(1)

    data = r.json()
    latest = data['artifacts'][0]
    download_url = latest['archive_download_url']
    logging.debug(download_url)

    # had to enable actions access on the token to get the artifact , so it probably won't work for other people's repos
    r = requests.get(download_url, headers=headers)
    logging.debug(r)
    z = zipfile.ZipFile(io.BytesIO(r.content))
    files = {
        'gds' : "/tmp/runs/wokwi/results/final/gds/scan_wrapper.gds",
        'lef' : "/tmp/runs/wokwi/results/final/lef/scan_wrapper.lef",
        }

    logging.debug("{} {}".format(files['gds'], files['lef']))

    for filetype in ['gds', 'lef']:
        src = files[filetype]
        dst = os.path.join(filetype, "project_{:03}.{}".format(macro_number, filetype))
        shutil.copyfile(src, dst)


# create macro file & positions
def create_macro():
    start_x = 80
    start_y = 80
    step_x  = 135
    step_y  = 135   # pdn pitch is 90
    rows    = 25
    cols    = 20

    num_macros = 0

    with open("openlane/user_project_wrapper/macro.cfg", 'w') as fh:
        fh.write("scan_controller 80 80 N\n")
        for row in range(rows):
            for col in range(cols):
                # skip the space where the scan controller goes on the first row
                if row == 0 and col <= 1:
                    continue
                instance = "project_{:03} {:<4} {:<4} N\n".format(num_macros, start_x + col * step_x, start_y + row * step_y)
                fh.write(instance)

                num_macros += 1

    with open("openlane/user_project_wrapper/macro_power.tcl", 'w') as fh:
        fh.write('set ::env(FP_PDN_MACRO_HOOKS) "\\\n')
        fh.write("	")
        fh.write("scan_controller")
        fh.write(" vccd1 vssd1 vccd1 vssd1")
        fh.write(", \\\n")
        for i in range(num_macros):
            fh.write("	")
            fh.write("project_{:03}".format(i))
            fh.write(" vccd1 vssd1 vccd1 vssd1")
            if i != num_macros - 1:
                fh.write(", \\\n")
        fh.write('"\n')

    return num_macros


# instantiate inside user_project_wrapper
def instantiate(num_macros):
    assigns = """
    localparam NUM_MACROS = {};
    wire [NUM_MACROS:0] data, scan, latch, clk;
    wire [8:0] active_select = io_in[20:12];
    wire [7:0] inputs = io_in[28:21];
    wire [7:0] outputs;
    assign io_out[36:29] = outputs;
    wire ready;
    assign io_out[37] = ready;
    """

    scan_controller_template = """
    scan_controller #(.NUM_DESIGNS(NUM_MACROS)) scan_controller(
        .clk            (wb_clk_i),
        .reset          (wb_rst_i),
        .active_select  (active_select),
        .inputs         (inputs),
        .outputs        (outputs),
        .ready          (ready),
        .scan_clk       (clk[0]),
        .scan_data_out  (data[0]),
        .scan_data_in   (data[NUM_MACROS]),
        .scan_select    (scan[0]),
        .scan_latch_enable(latch[0]),
        .oeb            (io_oeb[37:29])
    );

    """
    lesson_template = """
    project_{name} #(.NUM_IOS(8)) project_{instance:03} (
        .clk_in          (clk  [{instance}]),
        .data_in         (data [{instance}]),
        .scan_select_in  (scan [{instance}]),
        .latch_enable_in (latch[{instance}]),
        .clk_out         (clk  [{next_instance}]),
        .data_out        (data [{next_instance}]),
        .scan_select_out (scan [{next_instance}]),
        .latch_enable_out(latch[{next_instance}])
        );
    """
    with open('upw_pre.v') as fh:
        pre = fh.read()

    with open('upw_post.v') as fh:
        post = fh.read()

    with open('verilog/rtl/user_project_wrapper.v', 'w') as fh:
        fh.write(pre)
        fh.write(assigns.format(num_macros))
        fh.write(scan_controller_template)
        for number in range(num_macros):
            # instantiate template
            instance = lesson_template.format(instance=number, next_instance=number + 1)
            fh.write(instance)
        fh.write(post)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="TinyTapeout")

    parser.add_argument('--list', help="list projects", action='store_const', const=True)
    parser.add_argument('--update-designs', help='fetch the project data', action='store_const', const=True)
    parser.add_argument('--update-config', help='fetch the project data', action='store_const', const=True)
    parser.add_argument('--debug', help="debug logging", action="store_const", dest="loglevel", const=logging.DEBUG, default=logging.INFO)

    args = parser.parse_args()

    # setup log
    log_format = logging.Formatter('%(asctime)s - %(levelname)-8s - %(message)s')
    # configure the client logging
    log = logging.getLogger('')
    # has to be set to debug as is the root logger
    log.setLevel(args.loglevel)

    # create console handler and set level to info
    ch = logging.StreamHandler(sys.stdout)
    # create formatter for console
    ch.setFormatter(log_format)
    log.addHandler(ch)

    if args.update_designs:
        # fetches the artifacts from a gitrepo, then copies the gds/lef to the correct place
        get_macros()


    if args.update_config:
        # create macros.cfg, extra_lefs_defs
        num_macros = create_macro()
        # instantiate in user_project_wrapper.v
        instantiate(num_macros)
