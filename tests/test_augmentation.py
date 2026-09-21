import unittest
from pathlib import Path
import numpy as np
from tools.training import augment
from tools.training.evaluation import confirmation_score, choose_threshold, stream_windows
from tools.training.train_wake import windows, select_training, training_weights


class AugmentationTests(unittest.TestCase):
    def test_centre_ignores_dc_offset_and_keeps_the_word(self):
        audio = np.full(32000, 6000., dtype=float)
        audio[21000:25000] += np.sin(np.arange(4000)*.3)*3000
        cut = np.frombuffer(windows(audio.astype('<i2').tobytes(), True)[0], dtype='<i2')
        energy = (cut.astype(float)-6000)**2
        centre = np.dot(np.arange(len(cut)), energy)/energy.sum()
        self.assertLess(abs(centre-8000), 1200)

    def test_positive_variants_are_finite_fixed_size_and_reproducible(self):
        signal = np.zeros(16000); signal[6000:10000] = np.sin(np.arange(4000)*.2)*.2
        raw = (signal*32767).astype('<i2').tobytes()
        a = augment.positive_variants(raw,np.random.default_rng(4),[])
        self.assertEqual(a,augment.positive_variants(raw,np.random.default_rng(4),[]))
        self.assertGreater(len(a),10)
        for pcm in a:
            self.assertEqual(len(pcm),32000)
            self.assertGreater(np.std(np.frombuffer(pcm,dtype='<i2')),0)

    def test_one_peak_does_not_count_as_a_confirmed_wake(self):
        self.assertAlmostEqual(confirmation_score([.1,.95,.2]),.2)
        self.assertAlmostEqual(confirmation_score([.1,.95,.9]),.9)
        self.assertEqual(confirmation_score([.99]),0)
        self.assertEqual(len(stream_windows(b'\0'*32000)),5)

    def test_threshold_can_recover_quiet_positives_below_point_five(self):
        y = [1,1,0,0]; scores = [.3,.4,.01,.02]
        threshold = choose_threshold(y,scores)
        self.assertLessEqual(threshold,.3)
        self.assertGreater(threshold,.02)

    def test_imported_cap_never_discards_personal_recordings_or_positives(self):
        rows = [{'path':Path(f'foreign-{i}.wav'),'category':'noise','label':0} for i in range(100)]
        rows += [{'path':Path('esp32-ours.wav'),'category':'noise','label':0},
                 {'path':Path('word.wav'),'category':'ferris','label':1}]
        # Restrict to this split: even personal held-out files cannot enter training.
        selected = select_training(rows,list(range(20))+[100,101],limit=5)
        self.assertEqual(len(selected),7)
        self.assertIn(100,selected); self.assertIn(101,selected)
        self.assertTrue(set(selected).issubset(set(range(20))|{100,101}))

    def test_imported_noise_and_long_clips_do_not_drown_local_negatives(self):
        records = [{'label':1,'path':'esp32-a.wav'}, {'label':0,'path':'esp32-b.wav'},
                   {'label':0,'path':'download.wav'}, {'label':0,'path':'esp32-c.wav'}]
        weights = training_weights(records,[(0,2,0),(2,4,1),(4,104,2),(104,114,3)])
        self.assertAlmostEqual(weights[:2].sum(),weights[2:].sum())
        self.assertAlmostEqual(weights[2:4].sum(),weights[104:114].sum())
        self.assertAlmostEqual((weights[2:4].sum()+weights[104:114].sum())/weights[2:].sum(),.7)

    def test_local_false_alarms_are_not_hidden_by_imported_negatives(self):
        labels = np.array([1,0]+[0]*100)
        scores = np.array([.9,.7]+[.01]*100)
        personal = np.array([True,True]+[False]*100)
        threshold = choose_threshold(labels,scores,personal=personal)
        self.assertGreater(threshold,.7)
        self.assertLessEqual(threshold,.9)

    def test_dc_offset_does_not_change_augmentation_or_create_padding_clicks(self):
        signal = np.zeros(16000); signal[6000:10000] = np.sin(np.arange(4000)*.2)*2000
        signal = signal.astype('<i2')
        clean = augment.positive_variants(signal.tobytes(), np.random.default_rng(9), [])
        offset = augment.positive_variants((signal+6000).astype('<i2').tobytes(), np.random.default_rng(9), [])
        self.assertEqual(len(clean),len(offset))
        for a,b in zip(clean,offset):
            # Float32 centering may change rounding by one PCM quantization step.
            difference = np.frombuffer(a,dtype='<i2').astype(int)-np.frombuffer(b,dtype='<i2')
            self.assertLessEqual(np.max(np.abs(difference)),1)
        for window in stream_windows(np.full(16000,6000,dtype='<i2').tobytes()):
            self.assertEqual(np.std(np.frombuffer(window,dtype='<i2')),0)
