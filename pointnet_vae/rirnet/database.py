#!/usr/bin/env python3

import multiprocessing as mp
import rirnet.roomgen as rg
import rirnet.acoustic_utils as au
import numpy as np
import yaml
import sys
import csv
import os
import random
import pandas as pd
import glob
import librosa
import matplotlib.pyplot as plt
import operator
import signal
import sys
from scipy.spatial.distance import cdist as cdist
from sklearn.utils import shuffle

db_setup_filename = 'db_setup.yaml'
db_mean_filename = 'mean.npy'
db_std_filename = 'std.npy'
audio_rel_path = '../../audio/chamber'
db_rel_path = '../database'
header=['data_path', 'target_path', 'room_corners', 'room_absorption', 'room_mics', 'room_source']


class RirGenerator:
    def __init__(self, db_setup, n_total):
        self.n_total = n_total
        self.i_total = 0
        self.db_setup = db_setup
        self.h_length = db_setup['mfcc_length']*256
        self.min_peaks_length = db_setup['min_peaks_length']
        self.discarded = 0
        self.output = mp.Queue()
        self.processes = [mp.Process(target=self.compute_room_proc) for x in range(db_setup['n_proc'])]
        for p in self.processes:
            p.start()

    def __iter__(self):
        return self

    def __next__(self):
        if self.i_total == self.n_total:
            raise StopIteration

        i_produced = 0
        h_list = []
        info_list = []
        peaks_list = []
        self.terminate_dead_proc()
        room = self.output.get()
        self.processes.append(mp.Process(target=self.compute_room_proc))
        new_proc = self.processes[-1]
        new_proc.start()

        for i_rir, rir in enumerate(room.rir):
            cut_rir = remove_leading_zeros(list(rir[0]))
            rir_length = len(cut_rir)
            peaks = room.peaks[i_rir]
            peaks_length = len(peaks[0])

            if rir_length > self.h_length:
                self.discarded += 1
                return self.__next__()
            else:
                rir = au.pad_to(cut_rir, self.h_length, 0)

                h_list.append(rir)
                if peaks_length < self.min_peaks_length:
                    self.discarded += 1
                    return self.__next__()
                #else:
                #    times = au.pad_to(peaks[0], self.peaks_length, np.max(peaks[0]))
                #    alphas = au.pad_to(peaks[1], self.peaks_length, np.min(peaks[1]))
                #    peaks = [times, alphas]

                peaks_list.append(peaks)
                info_list.append([room.corners, room.absorption, room.mic_array.R[:, i_rir], room.sources[0].position])

                i_produced += 1
                if self.i_total + i_produced == self.n_total:
                    for process in self.processes:
                        process.terminate()
                    break
                if interrupted:
                    for process in self.processes:
                        process.terminate()
                    print('Terminated processes')
                    sys.exit()
        self.i_total += i_produced
        return h_list, peaks_list, info_list

    def terminate_dead_proc(self):
        while self.processes and not self.processes[0].is_alive():
            p = self.processes.pop(0)
            p.terminate()

    def compute_room_proc(self):
        room = rg.generate_from_dict(self.db_setup)
        room.compute_rir()
        peaks = self.compute_peaks(room)
        room.peaks = peaks
        self.output.put(room)

    def compute_peaks(self, room):
        if room.visibility is None:
            room.image_source_model()
        peaks = []
        for m, mic in enumerate(room.mic_array.R.T):
            distances = room.sources[0].distance(mic)
            times = distances/343.0*room.fs
            alphas = room.sources[0].damping / (4.*np.pi*distances)
            slice = tuple(np.where(room.visibility[0][m] == 1))
            alphas = alphas[slice]
            times = times[slice]

            if self.db_setup['order_sorting']:
                orders = room.sources[0].orders[slice]

                ordered_inds = []
                for order in range(min(orders), max(orders)):
                    order_inds = np.where(orders == order)[0]
                    time_inds = np.argsort(times[order_inds])
                    for ind in order_inds[time_inds]:
                        ordered_inds.append(ind)
                peaks.append([times[ordered_inds] - min(times[ordered_inds]), alphas[ordered_inds]])
            else:
                peaks.append([times - min(times), alphas])
        return peaks


def remove_leading_zeros(rir):
    ind_1st_nonzero = next((i for i, x in enumerate(rir) if x), None)
    rir[0:ind_1st_nonzero] = []
    return np.array(rir)


def generate_waveforms(wav, h_list):
    data_list = []
    target_list = []

    for i_h, h in enumerate(h_list):
        y = au.convolve(wav, h)
        y_length = au.next_power_of_two(np.size(y))
        data = au.pad_to(y, y_length, 0)
        target = au.pad_to(h, y_length, 0)
        target_list.append(target)
        data_list.append(data)
    return np.array(target_list), np.array(data_list)


def load_wavs(audio_folder, db_setup):
    audio_list = db_setup['source_audio']
    rate = db_setup['fs']
    wav_list = []
    if audio_list == ['']:
        audio_list = glob.glob(os.path.join(audio_folder, '*.wav'))
    for audio_filename in audio_list:
        wav_path = os.path.join(audio_folder, audio_filename)
        wav = au.normalize(au.read_wav(wav_path, rate)[0])
        wav_list.append(wav)
    return wav_list


def waveforms_to_mfccs(waveforms, db_setup):
    fs = db_setup['fs']
    n_mfcc = db_setup['n_mfcc']
    mfccs = [au.waveform_to_mfcc(waveform, fs, n_mfcc)[1][:,:-1] for waveform in waveforms]
    return mfccs


def pad_list_to_pow2(h_list):
    longest_irf = len(max(h_list, key=len))
    target_length = au.next_power_of_two(longest_irf)
    h_list = [au.pad_to(h, target_length) for h in h_list]
    return h_list


def calculate_delta_features(data_list):
    delta_list = []
    delta_2_list = []
    for data in data_list:
        delta_list.append(librosa.feature.delta(data))
        delta_2_list.append(librosa.feature.delta(data, order=2))
    return delta_list, delta_2_list


def parse_yaml(filename):
    with open(filename, 'r') as stream:
        db_setup = yaml.load(stream)
    return db_setup


def save_mean_std(db_csv_path, mean, dataset):
    db_csv_folder, _ = os.path.split(db_csv_path)

    df = pd.read_csv(db_csv_path)
    n_rows = df.shape[0]
    std_sum = np.zeros_like(mean)

    if dataset == 'data':
        col = 0
        for i in range(n_rows):
            path = df.iloc[i, col]
            data = np.load(path)
            std_sum += sum([(d.T-mean)**2 for d in data.T])
        n_std = n_rows * np.shape(data)[-1]
    elif dataset == 'target':
        col = 1
        for i in range(n_rows):
            path = df.iloc[i, col]
            data = np.load(path)
            std_sum += (data-mean)**2
        n_std = n_rows
    else:
        print('How did you even get here?!')

    std = np.sqrt(std_sum/(n_std-1))

    np.save(os.path.join(db_csv_folder, 'std_{}.npy'.format(dataset)), std)
    np.save(os.path.join(db_csv_folder, 'mean_{}.npy'.format(dataset)), mean)


def build(wav_list, db_csv_path, db_setup, n_total, data_folder_path):
    print('started building')
    rir_generator = RirGenerator(db_setup, n_total)
    with open(db_csv_path, 'w') as csvfile:
        writer = csv.writer(csvfile, delimiter=',')
        writer.writerow(header)

    db_data_mean = np.array([])
    db_target_mean = np.array([])

    while rir_generator.i_total < n_total:
        for h_list, peaks_list, info_list in rir_generator:
            counter = rir_generator.i_total/rir_generator.n_total*100
            print('Progress: {:5.01f}%, Discarded {} times.'.format(counter, rir_generator.discarded), end="\r")

            wav = random.choice(wav_list)
            target_list, data_list = generate_waveforms(wav, h_list)

            data_list = waveforms_to_mfccs(data_list, db_setup)
            target_list = waveforms_to_mfccs(target_list, db_setup)

            if db_setup['delta_features']:
                delta_1_list, delta_2_list = calculate_delta_features(data_list)
                data_list = [[data, delta_1, delta_2] for data, delta_1, delta_2 in zip(data_list, delta_1_list, delta_2_list)]
            else:
                data_list = [[data] for data in data_list]

            target_list = [[target] for target in target_list]

            if np.size(db_data_mean) == 0:
                db_data_mean = np.zeros(np.shape(data_list)[1:3])
                db_target_mean = np.zeros_like(target_list[0])
                print('Started building db with data of size {} and peaks of size {}'.format(np.shape(data_list[0]), np.shape(peaks_list[0])))
            #print('Progress: {:5.01f}%, Discarded {} times.'.format(counter, rir_generator.discarded), end="\r")

            n = db_setup['n_samples_val']
            db_data_mean += np.sum(data_list, axis=(0, 3))/(n*np.shape(data_list)[-1])
            db_target_mean += np.sum(target_list, axis=0)/n

            with open(db_csv_path, 'a') as csvfile:
                writer = csv.writer(csvfile, delimiter=',')
                n_saved = rir_generator.i_total - len(data_list)
                for i, data in enumerate(data_list):
                    peaks = peaks_list[i]
                    corners, absorption, mics, sources = info_list[i]
                    data_filename = '{}_d.npy'.format(n_saved + i)
                    peaks_filename = '{}_p.npy'.format(n_saved + i)
                    data_path = os.path.join(data_folder_path, data_filename)
                    peaks_path = os.path.join(data_folder_path, peaks_filename)
                    np.save(data_path, data)
                    np.save(peaks_path, peaks)
                    writer.writerow([data_path, peaks_path, corners, absorption, mics, sources])
    return db_data_mean, db_target_mean

def main():
    global interrupted
    interrupted = False
    signal.signal(signal.SIGINT, signal_handler)

    root = os.path.abspath(db_rel_path)

    db_csv_path_val = os.path.join(root, 'db-val.csv')
    db_csv_path_train = os.path.join(root, 'db-train.csv')

    audio_path_val = os.path.join(root, audio_rel_path, 'val')
    audio_path_train = os.path.join(root, audio_rel_path, 'train')

    data_folder_path_val = os.path.join(root, db_rel_path, 'val_data')
    data_folder_path_train = os.path.join(root, db_rel_path, 'train_data')

    if not os.path.exists(data_folder_path_val):
        os.mkdir(data_folder_path_val)

    if not os.path.exists(data_folder_path_train):
        os.mkdir(data_folder_path_train)

    db_setup_path = os.path.join(root, db_setup_filename)
    db_setup = parse_yaml(db_setup_path)

    wav_list_val = load_wavs(audio_path_val, db_setup)
    wav_list_train = load_wavs(audio_path_train, db_setup)

    _, _ = build(wav_list_val, db_csv_path_val, db_setup, db_setup['n_samples_val'], data_folder_path_val)
    db_data_mean, _ = build(wav_list_train, db_csv_path_train, db_setup, db_setup['n_samples_train'], data_folder_path_train)

    print('\nDatabase generated, Normalizing...')
    save_mean_std(db_csv_path_train, db_data_mean, 'data')
    #save_mean_std(db_csv_path, db_target_mean, 'target')
    print('Done')


def signal_handler(signal, frame):
    global interrupted
    interrupted = True

if __name__ == "__main__":
    main()
