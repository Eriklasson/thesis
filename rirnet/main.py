from __future__ import print_function
import sys
from rirnet_database import RirnetDatabase
import torch
import torch.nn.functional as F
import torch.optim as optim
import matplotlib.pyplot as plt
import os
import csv
import pandas as pd
from datetime import datetime, timedelta
import time
import numpy as np
from importlib import import_module
from glob import glob

#TODO constants should be added to self.args in net_master.py

# -------------  Initialization  ------------- #
class Model:
    def __init__(self, model_dir):
        self.model_dir = model_dir
        sys.path.append(model_dir)
        net = import_module('net')
        self.model = net.Net()
        self.args = self.model.args()
        torch.manual_seed(self.args.seed)

        use_cuda = not self.args.no_cuda and torch.cuda.is_available()
        self.device = torch.device("cuda" if use_cuda else "cpu")
        self.model.to(self.device)
        self.kwargs = {'num_workers': 1, 'pin_memory': True} if use_cuda else {}

        list_epochs = glob('*.pth')
        if list_epochs == []:
            epoch = 0
        else:
            epoch = max([int(e.split('.')[0]) for e in list_epochs])
            self.model.load_state_dict(torch.load(os.path.join(model_dir, '{}.pth'.format(str(epoch)))))


        self.epoch = epoch
        self.csv_path = os.path.join(self.args.db_path, 'db.csv')
        data_transform = self.model.transform()

        train_db = RirnetDatabase(is_training = True, args = self.args, transform = data_transform)
        eval_db = RirnetDatabase(is_training = False, args = self.args, transform = data_transform)

        self.train_loader = torch.utils.data.DataLoader(train_db, batch_size=self.args.batch_size, shuffle=True, **self.kwargs)
        self.eval_loader = torch.utils.data.DataLoader(eval_db, batch_size=self.args.batch_size, shuffle=True, **self.kwargs)
        self.optimizer = optim.SGD(self.model.parameters(), lr=self.args.lr, momentum=self.args.momentum)


    def train(self):
        self.model.train()
        for batch_idx, (source, target) in enumerate(self.train_loader):
            source, target = source.to(self.device), target.to(self.device)
            #source = source.cuda(args.gpu, non_blocking=True)
            self.optimizer.zero_grad()
            output = self.model(source)
            self.train_loss = F.l1_loss(output, target)
            self.train_loss.backward()
            self.optimizer.step()
            if batch_idx % self.args.log_interval == 0:
                print('Train Epoch: {:5d} [{:5d}/{:5d} ({:4.1f}%)]\tLoss: {:.6f}'.format(
                    self.epoch + 1, batch_idx * len(source), len(self.train_loader.dataset),
                    100. * batch_idx / len(self.train_loader), self.train_loss.item()))

    def evaluate(self):
        self.model.eval()
        eval_loss = []
        correct = 0
        with torch.no_grad():
            for batch_idx, (source, target) in enumerate(self.eval_loader):
                source, target = source.to(self.device), target.to(self.device)
                output = self.model(source)
                eval_loss.append(F.l1_loss(output, target).item())

                # correct += output.eq(target.data).sum().item()
                # test_loss /= len(test_loader.dataset)
                # print('\nTest set: Average loss: {:.4f}, Accuracy: {}/{} ({:.0f}%)\n'.format(
                #    test_loss, test_loss, len(test_loader.dataset),
                #    100. * test_loss / len(test_loader.dataset)))
                # print('\nTest set: Average loss: {:.4f}, Accuracy: {}/{} ({:.0f}%)\n'.format(
                #    test_loss, correct, len(test_loader.dataset),
                #    100. * correct / len(test_loader.dataset)))
            self.mean_eval_loss = np.mean(eval_loss)

    def save_model(self):
        full_path = os.path.join(self.model_dir, '{}.pth'.format(str(self.epoch)))
        torch.save(self.model.state_dict(), full_path)


    def loss_to_file(self):
        with open('loss_over_epochs.csv', 'a') as csvfile:
            writer = csv.writer(csvfile, delimiter=',')
            writer.writerow([self.epoch, self.train_loss.item(), self.mean_eval_loss, datetime.now().strftime("%Y-%m-%d %H:%M:%S")])

    def generate_plot(self):
        frmt = "%Y-%m-%d %H:%M:%S"
        plot_data = pd.read_csv('loss_over_epochs.csv', header=None)
        epochs_raw, train_losses_raw, eval_losses_raw, times_raw = plot_data.values.T

        epochs = [int(epoch) for epoch in list(epochs_raw) if is_number(epoch)]
        train_losses = [float(loss) for loss in list(train_losses_raw) if is_number(loss)]
        eval_losses = [float(loss) for loss in list(eval_losses_raw) if is_number(loss)]

        if self.args.save_timestamps:
            total_time = timedelta(0, 0, 0)
            if np.size(times_raw) > 1:
                start_times = times_raw[epochs_raw == 'started']
                stop_times = times_raw[epochs_raw == 'stopped']
                for i_stop_time, stop_time in enumerate(stop_times):
                    total_time += datetime.strptime(stop_time, frmt) - datetime.strptime(start_times[i_stop_time], frmt)
                total_time += datetime.now() - datetime.strptime(start_times[-1], frmt)
                plt.title('Trained for {} hours and {:2d} minutes'.format(int(total_time.days/24 + total_time.seconds//3600), (total_time.seconds//60)%60))
        plt.xlabel('Epochs')
        plt.ylabel('Loss (MSE)')
        plt.semilogy(epochs, train_losses, label='Training Loss')
        plt.semilogy(epochs, eval_losses, label='Evaluation Loss')
        plt.legend()
        plt.savefig('loss_over_epochs.png')
        plt.close()


    def stop_session(self):
        with open('loss_over_epochs.csv', 'a') as csvfile:
            writer = csv.writer(csvfile, delimiter=',')
            writer.writerow(['stopped', 'stopped', 'stopped', datetime.now().strftime("%Y-%m-%d %H:%M:%S")])

    def start_session(self):
        with open('loss_over_epochs.csv', 'a') as csvfile:
            writer = csv.writer(csvfile, delimiter=',')
            writer.writerow(['started', 'started', 'started', datetime.now().strftime("%Y-%m-%d %H:%M:%S")])

def main(model_dir):
    model = Model(model_dir)
    model.start_session()
    try:
        for epoch in range(model.epoch, model.args.epochs + 1):
            model.train()
            model.evaluate()
            model.epoch = epoch+1
            model.loss_to_file()
            model.generate_plot()
            if epoch % model.args.save_interval == 0:
                model.save_model()
    except KeyboardInterrupt:
        print(' '+'-'*64, '\nEarly stopping\n', '-'*64)
        model.stop_session()
        model.save_model()
        #model.loss_to_file()
        #model.generate_plot()


def is_number(s):
    try:
        float(s)
        return True
    except ValueError:
        return False


if __name__ == '__main__':
    main(sys.argv[1])